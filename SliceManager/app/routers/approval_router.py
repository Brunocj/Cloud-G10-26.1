# app/routers/approval_router.py
"""
Flujo de aprobación humana de solicitudes de despliegue (REQ-JP-05 / REQ-AD-03).

Cuando un usuario sin privilegio de despliegue directo solicita desplegar,
el slice queda en PENDING_APPROVAL con slice_json["deploy_request"] y NO se
encola. Estos endpoints permiten al jefeProyecto (de ese proyecto) o a un
admin/superAdmin revisar, aprobar (→ se encola al placement) o rechazar
(→ estado REJECTED, editable y re-enviable por el alumno).

Prefijo /api/v1/requests para no chocar con las rutas /api/v1/slices/{id}.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import AvailabilityZone, Role, Slice, User, UserProject
from app.services.notification_hub import notification_hub
from app.services.placement_worker import placement_queue
from app.services.audit import audit

logger = logging.getLogger("SliceManager.Approvals")

router = APIRouter(prefix="/api/v1/requests", tags=["Deploy Approvals"])


class ReviewPayload(BaseModel):
    comment: Optional[str] = None


# ── Helpers ────────────────────────────────────────────────────────────────────

def _led_project_ids(db: Session, user_id: str) -> list[int]:
    """IDs de proyectos donde el usuario es jefeProyecto."""
    jefe_role = db.query(Role).filter(Role.role_name == "jefeProyecto").first()
    if not jefe_role:
        return []
    memberships = db.query(UserProject).filter(
        UserProject.user_id == user_id,
        UserProject.project_role_id == jefe_role.id,
    ).all()
    return [m.project_id for m in memberships]


def _slice_json(sl: Slice) -> dict:
    s_json = sl.slice_json or {}
    if isinstance(s_json, str):
        s_json = json.loads(s_json)
    return s_json


def _can_review(db: Session, user: CurrentUser, sl: Slice) -> bool:
    """admin/superAdmin siempre; jefeProyecto solo si lidera el proyecto del slice."""
    if user.can_manage_all():
        return True
    if user.role == "jefeProyecto" and sl.project_id is not None:
        return sl.project_id in _led_project_ids(db, user.user_id)
    return False


def _serialize_request(db: Session, sl: Slice) -> dict:
    s_json = _slice_json(sl)
    nodes = s_json.get("nodes", [])
    req   = s_json.get("deploy_request", {})

    owner = db.query(User).filter(User.id == sl.creator_id).first()
    owner_name = None
    if owner:
        parts = [p for p in (owner.fullname, owner.lastname) if p and p.strip()]
        owner_name = " ".join(parts) or owner.username or owner.email

    az = db.query(AvailabilityZone).filter(
        AvailabilityZone.id == req.get("availability_zone_id", sl.availability_zone_id)
    ).first()

    return {
        "slice_id":     sl.id,
        "slice_name":   sl.name,
        "status":       sl.status,
        "owner_id":     sl.creator_id,
        "owner_name":   owner_name or f"{sl.creator_id[:8]}…",
        "owner_email":  owner.email if owner else None,
        "project_id":   sl.project_id,
        "project_name": sl.project.name if sl.project else None,
        "zone_id":      req.get("availability_zone_id", sl.availability_zone_id),
        "zone_name":    az.name if az else None,
        "ttl_hours":    req.get("ttl_hours", float(sl.TTL) if sl.TTL else None),
        "motivo":       req.get("motivo"),
        "requested_at": req.get("requested_at"),
        "vm_count":     len(nodes),
        "total_vcpus":  sum(int(n.get("vcores", 0)) for n in nodes),
        "total_ram_mb": sum(float(n.get("ram", 0)) for n in nodes),
        "nodes":        nodes,
        "edges":        s_json.get("edges", []),
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/pending", status_code=200)
def list_pending_requests(
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Solicitudes en espera de aprobación humana.
      - admin/superAdmin → todas
      - jefeProyecto     → las de sus proyectos
      - usuario          → 403
    """
    if user.role == "usuario":
        raise HTTPException(status_code=403, detail="No tienes bandeja de aprobaciones.")

    query = db.query(Slice).filter(Slice.status == "PENDING_APPROVAL")

    if not user.can_manage_all():
        led = _led_project_ids(db, user.user_id)
        if not led:
            return []
        query = query.filter(Slice.project_id.in_(led))

    pending = []
    for sl in query.order_by(Slice.id.desc()).all():
        req = _slice_json(sl).get("deploy_request")
        # Solo las que esperan humano (needs_approval); los deploys directos
        # pasan por PENDING_APPROVAL unos segundos pero no llevan este flag.
        if req and req.get("needs_approval"):
            pending.append(_serialize_request(db, sl))
    return pending


@router.post("/{slice_id}/approve", status_code=200)
async def approve_request(
    slice_id: int,
    payload:  ReviewPayload,
    db:       Session     = Depends(get_db),
    user:     CurrentUser = Depends(get_current_user),
):
    sl = db.query(Slice).filter(Slice.id == slice_id).first()
    if not sl:
        raise HTTPException(status_code=404, detail="Slice no encontrado")
    if not _can_review(db, user, sl):
        raise HTTPException(status_code=403, detail="No puedes aprobar esta solicitud.")

    s_json = _slice_json(sl)
    req = s_json.get("deploy_request")
    if sl.status != "PENDING_APPROVAL" or not req or not req.get("needs_approval"):
        raise HTTPException(status_code=400, detail="El slice no tiene una solicitud pendiente de aprobación.")

    zone_id = req.get("availability_zone_id") or sl.availability_zone_id or 1

    # Registrar la revisión y desmarcar needs_approval
    req["needs_approval"] = False
    s_json["review"] = {
        "action":      "approved",
        "comment":     payload.comment or "",
        "reviewer_id": user.user_id,
        "reviewed_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    }
    s_json["deploy_request"] = req
    sl.slice_json = dict(s_json)
    db.commit()

    # Encolar hacia el placement (mismo camino que un deploy directo)
    await placement_queue.put({"slice_id": slice_id, "zone_id": zone_id})
    logger.info("[APPROVAL] ✅ Slice %s aprobado por %s… → encolado (zona=%s)",
                slice_id, user.user_id[:8], zone_id)
    audit(user.user_id, user.role, "Approvals", "request_approved",
          f"Solicitud del slice '{sl.name}' aprobada." + (f" Comentario: {payload.comment}" if payload.comment else ""),
          slice_id=slice_id, project_id=sl.project_id)

    await notification_hub.notify_user(sl.creator_id, {
        "type":     "request_approved",
        "slice_id": slice_id,
        "title":    "Solicitud aprobada",
        "message":  f"Tu slice \"{sl.name}\" fue aprobado y está siendo desplegado."
                    + (f" Comentario: {payload.comment}" if payload.comment else ""),
    })

    return {"status": "APPROVED", "message": "Solicitud aprobada y enviada a despliegue."}


@router.post("/{slice_id}/reject", status_code=200)
async def reject_request(
    slice_id: int,
    payload:  ReviewPayload,
    db:       Session     = Depends(get_db),
    user:     CurrentUser = Depends(get_current_user),
):
    if not payload.comment or not payload.comment.strip():
        raise HTTPException(status_code=400, detail="El comentario de evaluación es obligatorio al rechazar.")

    sl = db.query(Slice).filter(Slice.id == slice_id).first()
    if not sl:
        raise HTTPException(status_code=404, detail="Slice no encontrado")
    if not _can_review(db, user, sl):
        raise HTTPException(status_code=403, detail="No puedes rechazar esta solicitud.")

    s_json = _slice_json(sl)
    req = s_json.get("deploy_request")
    if sl.status != "PENDING_APPROVAL" or not req or not req.get("needs_approval"):
        raise HTTPException(status_code=400, detail="El slice no tiene una solicitud pendiente de aprobación.")

    req["needs_approval"] = False
    s_json["review"] = {
        "action":      "rejected",
        "comment":     payload.comment.strip(),
        "reviewer_id": user.user_id,
        "reviewed_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    }
    s_json["deploy_request"] = req
    sl.slice_json = dict(s_json)
    # REJECTED: el dueño puede editar el diseño y volver a solicitar.
    sl.status = "REJECTED"
    db.commit()

    logger.info("[APPROVAL] ❌ Slice %s rechazado por %s…", slice_id, user.user_id[:8])
    audit(user.user_id, user.role, "Approvals", "request_rejected",
          f"Solicitud del slice '{sl.name}' rechazada. Motivo: {payload.comment.strip()}",
          level="WARNING", slice_id=slice_id, project_id=sl.project_id)

    await notification_hub.notify_user(sl.creator_id, {
        "type":     "request_rejected",
        "slice_id": slice_id,
        "title":    "Solicitud rechazada",
        "message":  f"Tu slice \"{sl.name}\" fue rechazado. Motivo: {payload.comment.strip()}",
    })

    return {"status": "REJECTED", "message": "Solicitud rechazada."}
