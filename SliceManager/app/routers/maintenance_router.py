# app/routers/maintenance_router.py
"""
Mantenimiento de la BD de slices/VMs (REQ-AD) — ver `app/services/db_maintenance.py`.

  GET  /api/v1/maintenance/scan   → reporte de inconsistencias (admin+, solo lectura)
  POST /api/v1/maintenance/clean  → aplica correcciones (superAdmin, dry_run=True por defecto)
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require_roles
from app.database import get_db
from app.services import db_maintenance
from app.services.audit import audit

logger = logging.getLogger("SliceManager.Maintenance")

router = APIRouter(prefix="/api/v1/maintenance", tags=["Maintenance"])


class CleanRequest(BaseModel):
    categories: Optional[List[str]] = None   # None = todas las corregibles (ver db_maintenance.CATEGORIES)
    dry_run: bool = True


@router.get("/scan")
def scan_db(
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin", "superAdmin")),
):
    """Reporte de solo lectura: cuenta y detalla filas huérfanas/inconsistentes."""
    return db_maintenance.scan(db)


@router.post("/clean")
def clean_db(
    request: CleanRequest,
    db:      Session     = Depends(get_db),
    user:    CurrentUser = Depends(require_roles("superAdmin")),
):
    """
    Aplica la limpieza. Con dry_run=True (default) no escribe nada — solo
    devuelve cuántas filas se tocarían por categoría, para revisar antes de
    confirmar con dry_run=False.
    """
    try:
        result = db_maintenance.clean(
            db,
            categories=set(request.categories) if request.categories else None,
            dry_run=request.dry_run,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if not request.dry_run:
        logger.warning("[MAINT] Limpieza de BD aplicada por %s… (rol=%s): %s",
                       user.user_id[:8], user.role, result["summary"])
        audit(user.user_id, user.role, "Maintenance", "db_cleaned",
              f"Limpieza de BD aplicada: {result['summary']}", level="WARNING")
    return result
