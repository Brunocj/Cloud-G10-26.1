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
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Flavor, UserProject, Vm
from app.auth import CurrentUser, get_current_user

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


# ── Helpers ───────────────────────────────────────────────────────────────────

def _user_project_ids(db: Session, user_id: str) -> set:
    rows = db.query(UserProject.project_id).filter(UserProject.user_id == user_id).all()
    return {r[0] for r in rows}


def _is_project_lead(db: Session, user_id: str, project_id: int) -> bool:
    """True si el usuario es jefe (project_role_id=2) del proyecto indicado."""
    row = (
        db.query(UserProject)
        .filter(UserProject.user_id == user_id, UserProject.project_id == project_id)
        .first()
    )
    return bool(row and row.project_role_id == 2)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[FlavorResponse])
def list_flavors(db: Session = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    """Lista los flavors que el usuario puede elegir (global + propios + de sus proyectos)."""
    q = db.query(Flavor).filter(Flavor.is_deleted == 0)

    if current_user.can_manage_all():
        # admin/superAdmin ven todos
        flavors = q.all()
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

    # Reglas de autorización por visibilidad
    if vis == "global" and not current_user.can_manage_all():
        raise HTTPException(403, "Solo administradores pueden crear flavors globales.")

    if vis == "project":
        if payload.project_id is None:
            raise HTTPException(400, "Un flavor 'project' requiere project_id.")
        # Debe ser jefe del proyecto o admin
        if not current_user.can_manage_all() and not _is_project_lead(db, current_user.user_id, payload.project_id):
            raise HTTPException(403, "Solo el jefe del proyecto (o un admin) puede crear flavors de proyecto.")

    flavor = Flavor(
        name=payload.name.strip(),
        vcpus=payload.vcpus,
        ram_mb=payload.ram_mb,
        disk_gb=payload.disk_gb,
        visibility=vis,
        owner_user_id=current_user.user_id,
        project_id=payload.project_id if vis == "project" else None,
        is_deleted=0,
        date_creation=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    )
    db.add(flavor)
    db.commit()
    db.refresh(flavor)
    logger.info("[Flavors] '%s' creado (%dvCPU/%.0fMB/%.0fGB, %s) por %s",
                flavor.name, flavor.vcpus, float(flavor.ram_mb), float(flavor.disk_gb),
                vis, current_user.user_id[:8])
    return FlavorResponse(
        id=flavor.id, name=flavor.name, vcpus=flavor.vcpus,
        ram_mb=float(flavor.ram_mb), disk_gb=float(flavor.disk_gb),
        visibility=flavor.visibility, owner_user_id=flavor.owner_user_id,
        project_id=flavor.project_id, is_owner=True, editable=True,
    )


@router.delete("/{flavor_id}", status_code=200)
def delete_flavor(flavor_id: int,
                  db: Session = Depends(get_db),
                  current_user: CurrentUser = Depends(get_current_user)):
    """
    Soft-delete de un flavor. Solo el dueño o un admin.
    Las VMs existentes NO se afectan: ya tienen el snapshot (flavor_name + specs).
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
    return {"message": f"Flavor '{flavor.name}' eliminado. Las VMs que lo usaron conservan sus recursos."}
