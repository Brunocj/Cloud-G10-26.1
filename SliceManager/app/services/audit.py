# app/services/audit.py
"""
Helper central de auditoría (REQ-JP-09 / REQ-AD-08).

Uso:
    from app.services.audit import audit
    audit(db, actor=user, module="SliceManager", action="deploy_requested",
          detail=f"Slice '{name}' solicitado", slice_id=id, project_id=pid)

Nunca lanza excepción: un fallo de auditoría no debe romper la operación.
Usa su propia sesión para no interferir con transacciones del caller.
"""
import logging
from datetime import datetime
from typing import Optional

from app.database import SessionLocal
from app.models import AuditLog

logger = logging.getLogger("SliceManager.Audit")


def audit(
    actor_id: str,
    actor_role: str,
    module: str,
    action: str,
    detail: str,
    level: str = "INFO",
    slice_id: Optional[int] = None,
    project_id: Optional[int] = None,
) -> None:
    db = SessionLocal()
    try:
        db.add(AuditLog(
            timestamp=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
            level=level,
            actor_id=actor_id or "system",
            actor_role=actor_role or "system",
            module=module,
            action=action,
            detail=(detail or "")[:500],
            slice_id=slice_id,
            project_id=project_id,
        ))
        db.commit()
    except Exception as exc:
        logger.warning("[AUDIT] No se pudo registrar evento '%s': %s", action, exc)
        db.rollback()
    finally:
        db.close()
