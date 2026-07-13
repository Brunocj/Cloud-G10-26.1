# app/routers/flavor_router.py
"""
Router para gestión de flavors (plantillas de recursos vCPU/RAM/disco).

Modelo de visibilidad:
  - global   → visible para todos; solo admin/superAdmin lo crean.
  - private  → visible solo para su creador (owner_user_id).
  - project  → visible para miembros del project_id; lo crean jefeProyecto/admin.

Endpoints:
  - GET    /utils/flavors        → Lista los flavors visibles para el usuario
  - POST   /utils/flavors        → Crea un flavor (según su rol/visibilidad)
  - DELETE /utils/flavors/{id}   → Soft-delete (dueño o admin)

El flavor Nova real se materializa perezosamente al desplegar en OpenStack
(ver ComputeProvisioner); aquí solo se gestiona la tabla lógica.
"""

import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Flavor, Project, UserProject, Vm
from app.auth import CurrentUser, get_current_user
from app.services.permissions import is_project_leader

logger = logging.getLogger("SliceManager.Flavors")

router = APIRouter(prefix="/api/v1/slices/utils/flavors", tags=["Flavor Management"])

_VALID_VISIBILITIES = ("global", "private", "project")
_ACTIVE_VM_STATES = ("DRAFT", "PROVISIONING", "ACTIVE", "PENDING_APPROVAL")


# ── Schemas ───────────────────────────────────────────────────────────────────

class FlavorCreate(BaseModel):
    name:       str   = Field(..., min_length=1, max_length=100)
    vcpus:      int   = Field(..., ge=1, le=128)
    ram_mb:     float = Field(..., ge=128)
    disk_gb:    float = Field(..., ge=1)
    visibility: str   = Field(default="private")
    project_id: Optional[int] = Field(default=None)


class FlavorResponse(BaseModel):
    id:         int
    name:       str
    vcpus:      int
    ram_mb:     float
    disk_gb:    float
    visibility: str
    owner_user_id: Optional[str] = None
    project_id: Optional[int] = None
    is_owner:   bool = False
    editable:   bool = False   # el usuario puede borrarlo
    provider_flavor_id: Optional[str] = None   # UUID Nova, si ya está materializado


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user_project_ids(db: Session, user_id: str) -> set:
    rows = db.query(UserProject.project_id).filter(UserProject.user_id == user_id).all()
    return {r[0] for r in rows}


def _sanitize_flavor_name(name: str) -> str:
    """Nombre legible para Nova: solo alfanuméricos/._- , máx 60 chars."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-")
    return cleaned[:60] or "flavor"


def _connect_openstack():
    """Conexión openstacksdk con las credenciales admin del entorno, o None si no están configuradas."""
    auth_url = os.getenv("OS_AUTH_URL")
    if not auth_url:
        return None
    import openstack
    return openstack.connect(
        auth_url=auth_url,
        username=os.getenv("OS_USERNAME", "admin"),
        password=os.getenv("OS_PASSWORD", ""),
        project_name=os.getenv("OS_PROJECT_NAME", "admin"),
        user_domain_name=os.getenv("OS_USER_DOMAIN_NAME", "Default"),
        project_domain_name=os.getenv("OS_PROJECT_DOMAIN_NAME", "Default"),
    )


def _materialize_openstack_flavor(vcpus: int, ram_mb: float, disk_gb: float, name: Optional[str] = None) -> Optional[str]:
    """
    Crea de inmediato el flavor Nova correspondiente a estos specs.

    Solo se llama para flavors creados por admin/superAdmin: la idea es que el
    catálogo "oficial" quede listo en `openstack flavor list` sin esperar al
    primer despliegue. El resto de roles sigue el camino lazy de siempre
    (ComputeProvisioner._ensure_flavor_uuid materializa recién cuando el
    deploy que usa el flavor es aprobado y realmente se lanza).

    Si se pasa `name`, se usa (saneado) como nombre del flavor Nova para que
    sea reconocible en `openstack flavor list` — se prioriza sobre la
    reutilización por specs exactos, porque el admin lo eligió explícitamente
    y dos flavors lógicos con nombres distintos deberían verse distintos en
    Nova aunque compartan specs. Sin nombre (o si falla), cae al fallback de
    "specs exactos" (misma estrategia que
    ComputeProvisioner.openstack_compute_executor._ensure_flavor_uuid).

    Fail-safe: si OpenStack no está disponible o falla, NO bloquea la
    creación del flavor lógico (podría ser para Linux Cluster, donde Nova ni
    se usa) — simplemente queda sin materializar y se resuelve lazy después.
    """
    try:
        conn = _connect_openstack()
        if conn is None:
            logger.warning("[OpenStack] OS_AUTH_URL no configurado — flavor queda sin materializar (se resolverá lazy en el deploy).")
            return None

        vcpus_i = int(vcpus)
        ram_i   = int(round(float(ram_mb)))
        disk_i  = int(round(float(disk_gb))) if disk_gb else 1

        # 1. Nombre propio → crear directo con ese nombre (o una variante
        #    desambiguada por specs si el nombre ya existe con otras specs).
        if name:
            base_name = _sanitize_flavor_name(name)
            for candidate in (base_name, f"{base_name}-{vcpus_i}c{ram_i}m{disk_i}g"):
                try:
                    created = conn.compute.create_flavor(
                        name=candidate, vcpus=vcpus_i, ram=ram_i, disk=disk_i, is_public=True,
                    )
                    logger.info("[OpenStack] Flavor Nova creado eager: %s (UUID=%s)", candidate, created.id)
                    return created.id
                except Exception:
                    existing = next((f for f in conn.compute.flavors() if f.name == candidate), None)
                    if (existing and existing.vcpus == vcpus_i and int(existing.ram) == ram_i
                            and int(getattr(existing, "disk", 0) or 0) == disk_i):
                        logger.info("[OpenStack] Flavor Nova reutilizado por nombre: %s", existing.id)
                        return existing.id
            logger.warning("[OpenStack] No se pudo crear el flavor Nova con nombre '%s' — usando fallback genérico.", base_name)

        # 2. Sin nombre (o el paso anterior falló) → specs exactos
        for flavor in conn.compute.flavors():
            if (flavor.vcpus == vcpus_i and int(flavor.ram) == ram_i
                    and int(getattr(flavor, "disk", 0) or 0) == disk_i):
                logger.info("[OpenStack] Flavor Nova reutilizado (eager, por specs): %s", flavor.id)
                return flavor.id

        generic_name = f"pucp-{vcpus_i}c-{ram_i}m-{disk_i}g"
        try:
            created = conn.compute.create_flavor(
                name=generic_name, vcpus=vcpus_i, ram=ram_i, disk=disk_i, is_public=True,
            )
            logger.info("[OpenStack] Flavor Nova creado eager: %s (UUID=%s)", generic_name, created.id)
            return created.id
        except Exception:
            for flavor in conn.compute.flavors():
                if flavor.name == generic_name:
                    return flavor.id
            raise

    except Exception as exc:
        logger.error("[OpenStack] No se pudo materializar el flavor en Nova de inmediato (quedará lazy): %s", exc)
        return None


def _fetch_openstack_flavors() -> List[dict]:
    """
    Devuelve los flavors existentes en Nova como dicts normalizados.
    Fail-safe: si OpenStack no está disponible, retorna lista vacía (no lanza).
    """
    try:
        conn = _connect_openstack()
        if conn is None:
            return []
        return [
            {
                "os_id":   f.id,
                "name":    f.name,
                "vcpus":   f.vcpus,
                "ram_mb":  f.ram,
                "disk_gb": getattr(f, "disk", 0) or 0,
            }
            for f in conn.compute.flavors()
        ]
    except Exception as exc:
        logger.error("[OpenStack] Error listando flavors de Nova: %s", exc)
        return []


def _delete_openstack_flavor(provider_flavor_id: str) -> None:
    """
    Elimina un flavor de Nova por su UUID. Best-effort: si OpenStack no está
    disponible o la llamada falla, solo se loggea — no bloquea el soft-delete
    lógico del flavor (mismo criterio fail-safe que `_delete_openstack_image`
    en image_router.py).
    """
    try:
        conn = _connect_openstack()
        if conn is None:
            return
        conn.compute.delete_flavor(provider_flavor_id)
        logger.info("[OpenStack] Flavor Nova %s eliminado.", provider_flavor_id)
    except Exception as exc:
        logger.warning("[OpenStack] No se pudo eliminar el flavor Nova %s: %s", provider_flavor_id, exc)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[FlavorResponse])
def list_flavors(db: Session = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    """
    Lista los flavors que el usuario puede elegir (global + propios + de sus proyectos).

    Para admin/superAdmin, además sincroniza flavors "huérfanos" que existen
    en Nova pero no están registrados en esta plataforma (creados a mano por
    fuera, o por despliegues previos a que existiera esta tabla) — así quedan
    visibles y se pueden borrar desde la web como cualquier otro flavor.
    """
    q = db.query(Flavor).filter(Flavor.is_deleted == 0)

    if current_user.can_manage_all():
        # admin/superAdmin ven todos
        flavors = q.all()

        tracked_provider_ids = {
            pid for (pid,) in db.query(Flavor.provider_flavor_id)
                .filter(Flavor.provider_flavor_id.isnot(None)).all()
        }
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                os_flavors = executor.submit(_fetch_openstack_flavors).result(timeout=10)
        except Exception as exc:
            logger.error("[OpenStack] Timeout o error listando flavors de Nova: %s", exc)
            os_flavors = []

        new_synced = 0
        for osf in os_flavors:
            if osf["os_id"] in tracked_provider_ids:
                continue
            orphan = Flavor(
                name=osf["name"],
                vcpus=int(osf["vcpus"]),
                ram_mb=float(osf["ram_mb"]),
                disk_gb=float(osf["disk_gb"] or 1),
                visibility="private",
                owner_user_id="system_sync",
                project_id=None,
                provider_flavor_id=osf["os_id"],
                is_deleted=0,
                date_creation=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            )
            db.add(orphan)
            db.flush()
            flavors.append(orphan)
            new_synced += 1
        if new_synced:
            db.commit()
            logger.info("[Flavors] %d flavor(s) de Nova sincronizados como huérfanos (no gestionados hasta ahora por la plataforma)", new_synced)
    else:
        my_projects = _user_project_ids(db, current_user.user_id)
        flavors = [
            f for f in q.all()
            if f.visibility == "global"
            or (f.visibility == "private" and f.owner_user_id == current_user.user_id)
            or (f.visibility == "project" and f.project_id in my_projects)
        ]

    result = []
    for f in flavors:
        is_owner = f.owner_user_id == current_user.user_id
        result.append(FlavorResponse(
            id=f.id, name=f.name, vcpus=f.vcpus,
            ram_mb=float(f.ram_mb), disk_gb=float(f.disk_gb),
            visibility=f.visibility, owner_user_id=f.owner_user_id,
            project_id=f.project_id,
            is_owner=is_owner,
            editable=is_owner or current_user.can_manage_all(),
            provider_flavor_id=f.provider_flavor_id,
        ))
    return result


@router.post("", status_code=201, response_model=FlavorResponse)
def create_flavor(payload: FlavorCreate,
                  db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(get_current_user)):
    """Crea un flavor respetando las reglas de visibilidad por rol."""
    vis = payload.visibility
    if vis not in _VALID_VISIBILITIES:
        raise HTTPException(400, f"visibility debe ser uno de {_VALID_VISIBILITIES}")

    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "El nombre del flavor no puede estar vacío.")

    # Reglas de autorización por visibilidad
    if vis == "global" and not current_user.can_manage_all():
        raise HTTPException(403, "Solo administradores pueden crear flavors globales.")

    if vis == "project":
        if payload.project_id is None:
            raise HTTPException(400, "Un flavor 'project' requiere project_id.")
        if not db.query(Project).filter(Project.id == payload.project_id).first():
            raise HTTPException(404, "El proyecto indicado no existe.")
        # Debe ser jefe del proyecto o admin
        if not current_user.can_manage_all() and not is_project_leader(db, current_user.user_id, payload.project_id):
            raise HTTPException(403, "Solo el jefe del proyecto (o un admin) puede crear flavors de proyecto.")

    flavor = Flavor(
        name=name,
        vcpus=payload.vcpus,
        ram_mb=payload.ram_mb,
        disk_gb=payload.disk_gb,
        visibility=vis,
        owner_user_id=current_user.user_id,
        project_id=payload.project_id if vis == "project" else None,
        is_deleted=0,
        date_creation=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    )

    # Admin/superAdmin: materializar el flavor Nova de inmediato (catálogo
    # oficial ya disponible en `openstack flavor list`). El resto de roles
    # sigue el camino lazy existente — se materializa recién cuando el
    # deploy que lo usa es aprobado y ComputeProvisioner lo lanza.
    if current_user.can_manage_all():
        flavor.provider_flavor_id = _materialize_openstack_flavor(
            payload.vcpus, payload.ram_mb, payload.disk_gb, name)

    db.add(flavor)
    db.commit()
    db.refresh(flavor)
    logger.info("[Flavors] '%s' creado (%dvCPU/%.0fMB/%.0fGB, %s) por %s%s",
                flavor.name, flavor.vcpus, float(flavor.ram_mb), float(flavor.disk_gb),
                vis, current_user.user_id[:8],
                f" — Nova UUID={flavor.provider_flavor_id}" if flavor.provider_flavor_id else "")
    return FlavorResponse(
        id=flavor.id, name=flavor.name, vcpus=flavor.vcpus,
        ram_mb=float(flavor.ram_mb), disk_gb=float(flavor.disk_gb),
        visibility=flavor.visibility, owner_user_id=flavor.owner_user_id,
        project_id=flavor.project_id, is_owner=True, editable=True,
        provider_flavor_id=flavor.provider_flavor_id,
    )


@router.delete("/{flavor_id}", status_code=200)
def delete_flavor(flavor_id: int,
                  db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(get_current_user)):
    """
    Soft-delete de un flavor. Solo el dueño o un admin.
    Las VMs existentes NO se afectan: ya tienen el snapshot (flavor_name + specs).

    Si el flavor tenía un flavor Nova materializado (`provider_flavor_id`,
    eager por un admin o cacheado tras un deploy), también se limpia de
    OpenStack — siempre que ningún otro flavor lógico activo siga apuntando
    al mismo UUID (Nova dedupea por specs exactos, así que varios flavors
    lógicos pueden compartir un mismo objeto Nova). Borrar el flavor Nova NO
    afecta a las VMs ya desplegadas con él: Nova copia los specs a la
    instancia al crearla.
    """
    flavor = db.query(Flavor).filter(Flavor.id == flavor_id, Flavor.is_deleted == 0).first()
    if not flavor:
        raise HTTPException(404, "Flavor no encontrado.")

    if flavor.owner_user_id != current_user.user_id and not current_user.can_manage_all():
        raise HTTPException(403, "No tiene permisos para eliminar este flavor.")

    flavor.is_deleted = 1
    db.commit()
    logger.info("[Flavors] id=%d '%s' soft-deleted por %s",
                flavor_id, flavor.name, current_user.user_id[:8])

    if flavor.provider_flavor_id:
        still_referenced = db.query(Flavor).filter(
            Flavor.id != flavor.id,
            Flavor.is_deleted == 0,
            Flavor.provider_flavor_id == flavor.provider_flavor_id,
        ).first()
        if not still_referenced:
            _delete_openstack_flavor(flavor.provider_flavor_id)
        else:
            logger.info("[Flavors] Flavor Nova %s no se elimina: aún lo usa el flavor lógico id=%d",
                        flavor.provider_flavor_id, still_referenced.id)

    return {"message": f"Flavor '{flavor.name}' eliminado. Las VMs que lo usaron conservan sus recursos."}
