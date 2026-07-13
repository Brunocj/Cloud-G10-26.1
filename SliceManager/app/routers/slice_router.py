# app/routers/slice_router.py
from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, require_roles
from app.database import get_db
from app.models import AvailabilityZone, Flavor, Image, IpPool, Slice, Vm, Worker, Role, UserProject
from app.repositories.slice_repo import SliceRepository
from app.schemas import DraftSaveRequest

logger = logging.getLogger("SliceManager.slices")

router = APIRouter(prefix="/api/v1/slices", tags=["Slices / Topologies"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _resolve_specs(db: Session, vm_data: dict) -> tuple:
    """
    Determina (vcore, ram_mb, disk_gb, flavor_id, flavor_name) para una VM.

    Si el nodo trae `flavor_id`, el flavor es la fuente de verdad de los
    recursos y se toma un SNAPSHOT (flavor_id + flavor_name copiados a la VM),
    de modo que borrar el flavor luego no altere la VM. Si no trae flavor,
    se respetan los valores sueltos del nodo (retro-compatibilidad).
    """
    flavor_id = vm_data.get("flavor_id")
    if flavor_id:
        flavor = db.query(Flavor).filter(
            Flavor.id == int(flavor_id), Flavor.is_deleted == 0).first()
        if flavor:
            return (int(flavor.vcpus), float(flavor.ram_mb), float(flavor.disk_gb),
                    flavor.id, flavor.name)
    return (int(vm_data.get("vcores", 1)), float(vm_data.get("ram", 512.0)),
            float(vm_data.get("disk", 5.0)), None, None)


def _validate_topology(db: Session, slice_json: dict, availability_zone_id: Optional[int] = None) -> None:
    """
    Valida la estructura de la topología antes de persistirla o desplegarla.

    Reglas:
      - Debe haber al menos 1 nodo.
      - Slices de una sola VM: SOLO permitidos en la zona "Linux Cluster"
        (en OpenStack un slice requiere ≥ 2 VMs conectadas).
      - Multi-VM: NO se permiten VMs huérfanas — todo nodo debe aparecer
        como extremo de al menos un edge.
      - Todo edge debe referenciar nodos existentes en la topología.

    Lanza HTTPException 400 con detalle si alguna regla falla.
    """
    nodes = slice_json.get("nodes", []) or []
    edges = slice_json.get("edges", []) or []

    if len(nodes) == 0:
        raise HTTPException(status_code=400,
                            detail="La topología debe contener al menos una VM.")

    node_ids = {n.get("id") for n in nodes}

    # Slice de una sola VM: solo Linux Cluster.
    # Si availability_zone_id es None (p. ej. draft sin AZ aún) se pospone
    # esta comprobación al momento del despliegue.
    if len(nodes) == 1:
        if availability_zone_id is not None:
            az = db.query(AvailabilityZone).filter(
                AvailabilityZone.id == availability_zone_id
            ).first()
            az_name = (az.name or "").strip().lower() if az else ""
            if "linux" not in az_name:
                raise HTTPException(
                    status_code=400,
                    detail="Los slices de una sola VM solo son válidos en la zona "
                           "Linux Cluster; OpenStack requiere al menos 2 VMs conectadas.",
                )
        return  # 1 VM: no hay edges que validar

    # Multi-VM: validar edges y detectar huérfanos
    connected: set = set()
    for e in edges:
        a = e.get("from", e.get("source"))
        b = e.get("to", e.get("target"))
        if a not in node_ids or b not in node_ids:
            raise HTTPException(
                status_code=400,
                detail=f"El enlace '{e.get('id')}' referencia nodos inexistentes.",
            )
        connected.add(a)
        connected.add(b)

    orphans = node_ids - connected
    if orphans:
        raise HTTPException(
            status_code=400,
            detail=f"Topología inválida: se detectaron VMs huérfanas (sin enlaces): "
                   f"{sorted(orphans)}. Toda VM debe estar conectada a al menos otra.",
        )


def _serialize_slice(t: Slice, users_map: dict = None, projects_map: dict = None) -> dict:
    """Serializa un objeto Slice a dict para la respuesta de la API."""
    s_json = t.slice_json if t.slice_json else {}
    if isinstance(s_json, str):
        s_json = json.loads(s_json)

    nodes = s_json.get("nodes", [])
    edges = s_json.get("edges", [])

    # Fusionar IP y VNC Port en los nodos del frontend
    deployed_vms = s_json.get("deployed_vms", [])
    if deployed_vms:
        for node in nodes:
            for d_vm in deployed_vms:
                if node.get("id") == d_vm.get("vm_id"):
                    node["worker_ip"]   = d_vm.get("worker_ip")
                    node["worker_port"] = d_vm.get("worker_port")   # puerto SSH al gateway
                    node["vnc_port"]    = d_vm.get("vnc_port")
                    node["vnc_url"]     = d_vm.get("vnc_url")       # token VNC de OpenStack (None en Linux Cluster)
                    node["external_ip"] = d_vm.get("external_ip")   # IP externa asignada (Linux Cluster o puerto provider de OpenStack)

    review = s_json.get("review")

    return {
        "id":        t.id,
        "name":      t.name if t.name else f"Slice {t.id}",
        "status":    t.status,
        "availability_zone_id": t.availability_zone_id,
        "owner_id":  t.creator_id,
        "owner_name":   (users_map or {}).get(t.creator_id),
        "project_id":   t.project_id,
        "project_name": (projects_map or {}).get(t.project_id),
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
        "vcpus":     sum(int(n.get("vcores", 0)) for n in nodes),
        "ramLabel":  f"{sum(float(n.get('ram', 0)) for n in nodes)} MB",
        "ttl_hours":        float(t.TTL) if t.TTL else None,
        "date_deployed":    t.date_deployed,
        "date_destruction": t.date_destruction,
        "review":        {"action": review.get("action"), "comment": review.get("comment"),
                          "reviewed_at": review.get("reviewed_at")} if review else None,
        "nodes":     nodes,
        "edges":     edges,
    }


def _assert_owner_or_admin(db_slice: Slice, user: CurrentUser, db: Session = None) -> None:
    """
    Lanza 403 si el usuario no puede operar el slice.
    Permitido: dueño, admin/superAdmin, o jefeProyecto líder del proyecto
    del slice (REQ-JP-07 — intervención directa "Modo Dios").
    """
    from app.services.permissions import can_operate_slice
    if db is not None:
        if can_operate_slice(db, user, db_slice):
            return
    elif user.is_owner_or_above(db_slice.creator_id, min_role="admin"):
        return
    raise HTTPException(
        status_code=403,
        detail="No tienes permiso para operar sobre el slice de otro usuario.",
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/", status_code=200)
def list_slices(
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Lista slices según el rol:
    - admin / superAdmin → TODOS los slices.
    - jefeProyecto → propios + los slices de proyectos donde figura como jefe.
    - usuario → solo los propios.
    """
    query = db.query(Slice).filter(Slice.status != "TEMPLATE")   # las plantillas tienen su propio listado

    if user.can_manage_all():
        pass  # sin filtro
    elif user.role == "jefeProyecto":
        jefe_role = db.query(Role).filter(Role.role_name == "jefeProyecto").first()
        leader_project_ids = []
        if jefe_role:
            leader_project_ids = [
                m.project_id for m in db.query(UserProject).filter(
                    UserProject.user_id == user.user_id,
                    UserProject.project_role_id == jefe_role.id,
                ).all()
            ]
        if leader_project_ids:
            query = query.filter(
                (Slice.creator_id == user.user_id) |
                (Slice.project_id.in_(leader_project_ids))
            )
        else:
            query = query.filter(Slice.creator_id == user.user_id)
    else:
        query = query.filter(Slice.creator_id == user.user_id)

    slices = query.order_by(Slice.id.desc()).all()

    # Resolver nombres de dueños y proyectos en 2 queries (evita N+1)
    from app.models import Project, User
    creator_ids = {s.creator_id for s in slices if s.creator_id}
    project_ids = {s.project_id for s in slices if s.project_id}

    users_map = {}
    if creator_ids:
        for u in db.query(User).filter(User.id.in_(creator_ids)).all():
            parts = [p for p in (u.fullname, u.lastname) if p and p.strip() and p.lower() != "none"]
            users_map[u.id] = " ".join(parts) or u.username or u.email

    projects_map = {}
    if project_ids:
        for p in db.query(Project).filter(Project.id.in_(project_ids)).all():
            projects_map[p.id] = p.name

    return [_serialize_slice(t, users_map, projects_map) for t in slices]


@router.post("/draft")
def create_draft(
    request: DraftSaveRequest,
    db:      Session     = Depends(get_db),
    user:    CurrentUser = Depends(get_current_user),
):
    """Crea un borrador de slice. Cualquier rol autenticado puede crear slices."""
    # Validación estructural de la topología (huérfanos, edges rotos, vacío).
    # La regla de "1 VM ⇒ Linux Cluster" se re-valida al desplegar (aquí no
    # hay AZ todavía).
    _validate_topology(db, request.slice_json, availability_zone_id=None)

    try:
        slice_creado = SliceRepository.save_draft(
            db=db,
            owner_id=user.user_id,          # ← ID real del JWT
            payload=request.slice_json,
            request_name=request.name,
        )

        workers_db  = db.query(Worker).all()
        num_workers = len(workers_db)
        nodes_list  = request.slice_json.get("nodes", [])

        for index, vm_data in enumerate(nodes_list):
            asignado = workers_db[index % num_workers] if num_workers > 0 else None

            ext_ip     = vm_data.get("external_ip", None)
            int_access = 1 if ext_ip else int(vm_data.get("internet_access", 0))

            img_id = vm_data.get("image_id")
            if img_id is not None and int(img_id) < 0:
                img_id = None

            vcore, ram_mb, disk_gb, fl_id, fl_name = _resolve_specs(db, vm_data)
            nueva_vm = Vm(
                name=vm_data.get("id"),
                vcore=vcore,
                ram=ram_mb,
                disk=disk_gb,
                flavor_id=fl_id,
                flavor_name=fl_name,
                state="DRAFT",
                slice_id=slice_creado.id,
                image_id=img_id,
                worker_id=asignado.id if asignado else None,
                external_ip=ext_ip,
                internet_access=int_access,
            )

            vm_data["worker"]    = asignado.name if asignado else "Unassigned"
            vm_data["worker_id"] = asignado.id if asignado else None

            db.add(nueva_vm)
            db.flush()

            # "random" es un centinela: se resuelve a una IP libre del pool de la
            # zona en el momento del despliegue (agnóstico Linux/OpenStack).
            if ext_ip and ext_ip != "random":
                ip_record = db.query(IpPool).filter(IpPool.ip_address == ext_ip).first()
                if not ip_record:
                    db.rollback()
                    raise HTTPException(status_code=400, detail=f"La IP '{ext_ip}' no existe en el pool.")
                if ip_record.is_used and ip_record.vm_id != nueva_vm.id:
                    db.rollback()
                    raise HTTPException(status_code=409, detail=f"La IP '{ext_ip}' ya está en uso por otra VM.")
                ip_record.is_used = 1
                ip_record.vm_id   = nueva_vm.id

        slice_creado.slice_json = {
            "nodes": nodes_list,
            "edges": request.slice_json.get("edges", []),
        }
        db.commit()

        logger.info(
            "Borrador creado: slice_id=%s owner=%s…",
            slice_creado.id, user.user_id[:8],
        )
        return {"message": "Borrador guardado con éxito", "slice_id": slice_creado.id}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{slice_id}/draft", status_code=200)
def update_draft(
    slice_id: int,
    request:  DraftSaveRequest,
    db:       Session     = Depends(get_db),
    user:     CurrentUser = Depends(get_current_user),
):
    """
    Actualiza un borrador.
    - Solo el dueño (o admin/superAdmin) puede editarlo.
    """
    db_slice = db.query(Slice).filter(Slice.id == slice_id).first()
    if not db_slice:
        raise HTTPException(status_code=404, detail="Slice no encontrado")

    if db_slice.status != "DRAFT":
        raise HTTPException(status_code=400, detail="Solo se pueden editar borradores")

    # ── Autorización de negocio ────────────────────────────────────
    _assert_owner_or_admin(db_slice, user, db)

    # Validación estructural de la topología. Si el draft ya tiene AZ asignada
    # (poco habitual en DRAFT, pero por si el flujo la fija), aplicamos también
    # la regla de "1 VM ⇒ Linux Cluster".
    _validate_topology(db, request.slice_json,
                       availability_zone_id=db_slice.availability_zone_id)

    # Liberar IPs antes de destruir VMs viejas
    vms_antiguas = db.query(Vm).filter(Vm.slice_id == slice_id).all()
    for v in vms_antiguas:
        if v.external_ip:
            ip_record = db.query(IpPool).filter(
                IpPool.ip_address == v.external_ip
            ).first()
            if ip_record:
                ip_record.is_used = 0
                ip_record.vm_id   = None

    db.query(Vm).filter(Vm.slice_id == slice_id).delete()

    for vm_data in request.slice_json.get("nodes", []):
        ext_ip     = vm_data.get("external_ip", None)
        int_access = 1 if ext_ip else int(vm_data.get("internet_access", 0))

        img_id = vm_data.get("image_id")
        if img_id is not None and int(img_id) < 0:
            img_id = None

        vcore, ram_mb, disk_gb, fl_id, fl_name = _resolve_specs(db, vm_data)
        nueva_vm = Vm(
            name=vm_data.get("id"),
            vcore=vcore,
            ram=ram_mb,
            disk=disk_gb,
            flavor_id=fl_id,
            flavor_name=fl_name,
            state="DRAFT",
            slice_id=slice_id,
            image_id=img_id,
            worker_id=vm_data.get("worker_id"),
            external_ip=ext_ip,
            internet_access=int_access,
        )
        db.add(nueva_vm)
        db.flush()

        # "random" se resuelve al desplegar (ver placement_worker) — agnóstico
        if ext_ip and ext_ip != "random":
            ip_record = db.query(IpPool).filter(IpPool.ip_address == ext_ip).first()
            if not ip_record:
                db.rollback()
                raise HTTPException(status_code=400, detail=f"La IP '{ext_ip}' no existe en el pool.")
            if ip_record.is_used and ip_record.vm_id != nueva_vm.id:
                db.rollback()
                raise HTTPException(status_code=409, detail=f"La IP '{ext_ip}' ya está en uso por otra VM.")
            ip_record.is_used = 1
            ip_record.vm_id   = nueva_vm.id

    # Guardar slice_json con nodes + edges DESPUÉS del loop,
    # igual que create_draft — así _serialize_slice puede leer los nodos.
    db_slice.slice_json = {
        "nodes": [vm_data for vm_data in request.slice_json.get("nodes", [])],
        "edges": request.slice_json.get("edges", []),
    }
    db.commit()
    logger.info(
        "Borrador actualizado: slice_id=%s por user=%s…",
        slice_id, user.user_id[:8],
    )
    return {"message": "Borrador actualizado con éxito"}


# ── Plantillas (REQ-US-04 / REQ-JP-03 / REQ-AD-05) ────────────────────────────
# Se almacenan como filas Slice con status="TEMPLATE" y template_type:
#   1 = personal (solo el creador)  ·  2 = de proyecto  ·  3 = global

_TPL_PERSONAL, _TPL_PROJECT, _TPL_GLOBAL = 1, 2, 3


from pydantic import BaseModel as _BM

class PublishTemplateRequest(_BM):
    name: str
    scope: str                      # "personal" | "project" | "global"
    project_id: Optional[int] = None


@router.get("/templates/list", status_code=200)
def list_templates(
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Plantillas visibles: globales + personales propias + de mis proyectos."""
    my_project_ids = [
        m.project_id for m in db.query(UserProject).filter(UserProject.user_id == user.user_id).all()
    ]
    templates = db.query(Slice).filter(Slice.status == "TEMPLATE").order_by(Slice.id.desc()).all()

    result = []
    for t in templates:
        visible = (
            t.template_type == _TPL_GLOBAL
            or (t.template_type == _TPL_PERSONAL and t.creator_id == user.user_id)
            or (t.template_type == _TPL_PROJECT and (t.project_id in my_project_ids or user.can_manage_all()))
        )
        if not visible:
            continue
        s_json = t.slice_json or {}
        if isinstance(s_json, str):
            s_json = json.loads(s_json)
        result.append({
            "id":            t.id,
            "name":          t.name,
            "scope":         {1: "personal", 2: "project", 3: "global"}.get(t.template_type, "personal"),
            "project_id":    t.project_id,
            "project_name":  t.project.name if t.project else None,
            "owner_id":      t.creator_id,
            "can_delete":    t.creator_id == user.user_id or user.can_manage_all(),
            "nodeCount":     len(s_json.get("nodes", [])),
            "edgeCount":     len(s_json.get("edges", [])),
            "nodes":         s_json.get("nodes", []),
            "edges":         s_json.get("edges", []),
        })
    return result


@router.post("/{slice_id}/publish-template", status_code=201)
def publish_template(
    slice_id: int,
    request:  PublishTemplateRequest,
    db:       Session     = Depends(get_db),
    user:     CurrentUser = Depends(get_current_user),
):
    """
    Convierte la topología de un slice en plantilla reutilizable.
      personal → cualquier usuario, sobre sus propios slices
      project  → jefeProyecto del proyecto (o admin+)
      global   → solo admin/superAdmin, y únicamente con imágenes generales
                 (REQ-AD-05: validación de integridad)
    """
    src = db.query(Slice).filter(Slice.id == slice_id).first()
    if not src:
        raise HTTPException(status_code=404, detail="Slice no encontrado")

    s_json = src.slice_json or {}
    if isinstance(s_json, str):
        s_json = json.loads(s_json)
    nodes = s_json.get("nodes", [])
    if not nodes:
        raise HTTPException(status_code=400, detail="El slice no tiene nodos para publicar.")

    from app.services.permissions import can_operate_slice, is_project_leader

    scope = request.scope
    tpl_project_id = None

    if scope == "personal":
        if not can_operate_slice(db, user, src):
            raise HTTPException(status_code=403, detail="No puedes publicar el slice de otro usuario.")
        tpl_type = _TPL_PERSONAL
    elif scope == "project":
        tpl_project_id = request.project_id or src.project_id
        if tpl_project_id is None:
            raise HTTPException(status_code=400, detail="Indica el proyecto destino de la plantilla.")
        if not (user.can_manage_all() or is_project_leader(db, user.user_id, tpl_project_id)):
            raise HTTPException(status_code=403, detail="Solo el jefe del proyecto (o admin) puede publicar plantillas de proyecto.")
        tpl_type = _TPL_PROJECT
    elif scope == "global":
        if not user.can_manage_all():
            raise HTTPException(status_code=403, detail="Solo admin/superAdmin publican plantillas globales.")
        # REQ-AD-05: una plantilla global no puede usar imágenes personales
        for n in nodes:
            img_id = n.get("image_id")
            if img_id is None or int(img_id) < 0:
                continue
            img = db.query(Image).filter(Image.id == int(img_id)).first()
            if img and not img.is_general:
                raise HTTPException(
                    status_code=400,
                    detail=f"Error de Integridad: el nodo '{n.get('id')}' usa la imagen personal "
                           f"'{img.name}'. Las plantillas globales solo admiten imágenes del catálogo global.",
                )
        tpl_type = _TPL_GLOBAL
    else:
        raise HTTPException(status_code=400, detail="scope debe ser personal | project | global")

    # Copia limpia del diseño: solo estructura, sin datos de despliegue ni IPs
    clean_nodes = []
    for n in nodes:
        cn = dict(n)
        for k in ("worker", "worker_id", "worker_ip", "worker_port", "vnc_port",
                  "vnc_url", "external_ip", "provider_instance_id"):
            cn.pop(k, None)
        clean_nodes.append(cn)

    tpl = Slice(
        name=request.name.strip() or f"Plantilla {slice_id}",
        status="TEMPLATE",
        template_type=tpl_type,
        creator_id=user.user_id,
        project_id=tpl_project_id,
        slice_json={"nodes": clean_nodes, "edges": s_json.get("edges", [])},
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)

    from app.services.audit import audit
    audit(user.user_id, user.role, "Templates", "template_published",
          f"Plantilla '{tpl.name}' publicada (scope={scope}) desde el slice '{src.name}'.",
          slice_id=slice_id, project_id=tpl_project_id)
    logger.info("Plantilla publicada: id=%s scope=%s por %s…", tpl.id, scope, user.user_id[:8])
    return {"template_id": tpl.id, "message": f"Plantilla '{tpl.name}' publicada."}


@router.delete("/templates/{template_id}", status_code=200)
def delete_template(
    template_id: int,
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    tpl = db.query(Slice).filter(Slice.id == template_id, Slice.status == "TEMPLATE").first()
    if not tpl:
        raise HTTPException(status_code=404, detail="Plantilla no encontrada")
    if not (tpl.creator_id == user.user_id or user.can_manage_all()):
        raise HTTPException(status_code=403, detail="No puedes eliminar esta plantilla.")
    name = tpl.name
    db.delete(tpl)
    db.commit()
    return {"message": f"Plantilla '{name}' eliminada."}


# ── Utils (sin restricción de rol — cualquier usuario autenticado) ─────────────



@router.get("/utils/workers", status_code=200)
def get_available_workers(
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),   # noqa: autenticado
):
    workers = db.query(Worker).all()
    if not workers:
        return ["server1"]
    return [w.name for w in workers]


@router.get("/utils/availability-zones", status_code=200)
def get_availability_zones(
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),   # noqa: autenticado
):
    zones = db.query(AvailabilityZone).all()
    return [{"id": z.id, "name": z.name} for z in zones]


@router.get("/utils/available-ips", status_code=200)
def get_available_ips(
    zone_id: Optional[int] = None,
    db:      Session     = Depends(get_db),
    user:    CurrentUser = Depends(get_current_user),   # noqa: autenticado
):
    query = db.query(IpPool).filter(IpPool.is_used == 0)
    if zone_id is not None:
        query = query.filter(IpPool.availability_zone_id == zone_id)
    ips = query.all()
    return sorted(
        (ip.ip_address for ip in ips),
        key=lambda addr: [int(octet) for octet in addr.split(".")],
    )


@router.get("/{slice_id}/vms/{vm_id}/console", status_code=200)
async def get_vm_console(
    slice_id: int,
    vm_id:    str,
    db:       Session     = Depends(get_db),
    user:     CurrentUser = Depends(get_current_user),
):
    """
    Pide un token de consola noVNC nuevo para una VM de OpenStack.
    Los tokens de nova-novncproxy expiran/se consumen rápido, así que se
    solicita uno fresco cada vez que el usuario abre la consola, en vez de
    reusar el guardado en el momento del deploy.
    """
    db_slice = db.query(Slice).filter(Slice.id == slice_id).first()
    if not db_slice:
        raise HTTPException(status_code=404, detail="Slice no encontrado")
    # Dueño, admin+, o jefeProyecto del proyecto (REQ-JP-07: consola de alumnos)
    _assert_owner_or_admin(db_slice, user, db)

    db_vm = db.query(Vm).filter(Vm.slice_id == slice_id, Vm.name == vm_id).first()
    if not db_vm or not db_vm.provider_instance_id:
        raise HTTPException(status_code=404, detail="La VM no tiene una instancia de OpenStack asociada.")

    from app.nats_producer import nats_producer
    result = await nats_producer.request_console_refresh(db_vm.provider_instance_id)
    if not result.get("vnc_url"):
        raise HTTPException(status_code=502, detail=result.get("error") or "No se pudo obtener la consola.")
    return {"vnc_url": result["vnc_url"]}