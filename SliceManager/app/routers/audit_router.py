# app/routers/audit_router.py
"""
Bitácora de eventos (REQ-JP-09 / REQ-AD-08).

  GET /api/v1/audit?limit=&level=&module=&q=&slice_id=

  - admin / superAdmin → todos los eventos
  - jefeProyecto       → solo eventos de sus proyectos (o propios)
  - usuario            → solo sus propios eventos
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user
from app.database import get_db
from app.models import AuditLog, User
from app.services.permissions import led_project_ids

logger = logging.getLogger("SliceManager.Audit")

router = APIRouter(prefix="/api/v1/audit", tags=["Audit"])


@router.get("/", status_code=200)
def list_audit_events(
    limit:    int = 200,
    level:    Optional[str] = None,
    module:   Optional[str] = None,
    q:        Optional[str] = None,
    slice_id: Optional[int] = None,
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    query = db.query(AuditLog)

    # ── Alcance por rol ──────────────────────────────────────────────
    if user.can_manage_all():
        pass
    elif user.role == "jefeProyecto":
        led = led_project_ids(db, user.user_id)
        conds = [AuditLog.actor_id == user.user_id]
        if led:
            conds.append(AuditLog.project_id.in_(led))
        query = query.filter(or_(*conds))
    else:
        query = query.filter(AuditLog.actor_id == user.user_id)

    # ── Filtros ──────────────────────────────────────────────────────
    if level:
        query = query.filter(AuditLog.level == level.upper())
    if module:
        query = query.filter(AuditLog.module == module)
    if slice_id is not None:
        query = query.filter(AuditLog.slice_id == slice_id)
    if q and q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(or_(
            AuditLog.detail.ilike(like),
            AuditLog.action.ilike(like),
            AuditLog.actor_id.ilike(like),
        ))

    rows = query.order_by(AuditLog.id.desc()).limit(min(max(limit, 1), 1000)).all()

    # Resolver nombres de actores en un solo query
    actor_ids = {r.actor_id for r in rows if r.actor_id and r.actor_id != "system"}
    users_map = {}
    if actor_ids:
        for u in db.query(User).filter(User.id.in_(actor_ids)).all():
            parts = [p for p in (u.fullname, u.lastname) if p and p.strip()]
            users_map[u.id] = " ".join(parts) or u.username or u.email

    return [{
        "id":         r.id,
        "timestamp":  r.timestamp,
        "level":      r.level,
        "actor_id":   r.actor_id,
        "actor_name": users_map.get(r.actor_id, "Sistema" if r.actor_id == "system" else f"{(r.actor_id or '')[:8]}…"),
        "actor_role": r.actor_role,
        "module":     r.module,
        "action":     r.action,
        "detail":     r.detail,
        "slice_id":   r.slice_id,
        "project_id": r.project_id,
    } for r in rows]
