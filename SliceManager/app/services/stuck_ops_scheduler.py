# app/services/stuck_ops_scheduler.py
"""
Vigía de operaciones de edición en caliente (modify) que se quedan colgadas.

Si QueueManager (o cualquier paso del saga: Placement/Network/Compute) nunca
responde a un extend/shrink, el slice queda en PROVISIONING para siempre y
las VMs nuevas (state=DRAFT) retienen su vnc_port indefinidamente — con solo
99 puertos por worker, esto agota el pool. Además, mientras `pending_extension`
o `pending_shrink` sigan seteados, `modify_active_slice` bloquea cualquier
nuevo intento de edición sobre ese slice (ver deploy_router.py).

Este scheduler revisa cada STUCK_OP_CHECK_INTERVAL_SECONDS los slices en
PROVISIONING con una operación pendiente más vieja que STUCK_OP_TIMEOUT_MINUTES
y la revierte, liberando los puertos/VMs draft y devolviendo el slice a ACTIVE
para que el usuario pueda reintentar.
"""
import asyncio
import json
import logging
import os
from datetime import datetime, timedelta

from app.database import SessionLocal
from app.models import Slice
from app.services.notification_hub import notification_hub
from app.services.audit import audit

logger = logging.getLogger("SliceManager.StuckOps")

STUCK_OP_CHECK_INTERVAL_SECONDS: int = int(os.getenv("STUCK_OP_CHECK_INTERVAL_SECONDS", "60"))
STUCK_OP_TIMEOUT_MINUTES: float = float(os.getenv("STUCK_OP_TIMEOUT_MINUTES", "10"))

_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


def _parse_date(raw):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except (ValueError, TypeError):
            continue
    return None


async def _revert_extension(db, db_slice: Slice, pending_ext: dict) -> None:
    from app.services.placement_worker import _fail_extend
    logger.warning("[STUCK-OPS] 🧩 Extensión del slice %s sin respuesta tras %.0f min — revirtiendo.",
                   db_slice.id, STUCK_OP_TIMEOUT_MINUTES)
    _fail_extend(db, db_slice, pending_ext)  # borra las VMs draft nuevas (libera su vnc_port) y vuelve a ACTIVE
    audit("system", "system", "StuckOpsWatchdog", "slice_extend_timeout",
          f"Slice '{db_slice.name}': extensión revertida por falta de respuesta.",
          level="WARNING", slice_id=db_slice.id, project_id=db_slice.project_id)
    await notification_hub.notify_user(db_slice.creator_id, {
        "type": "slice_failed", "slice_id": db_slice.id,
        "title": "Modificación revertida",
        "message": f"\"{db_slice.name}\" no recibió confirmación a tiempo — se revirtieron los cambios nuevos.",
    })


async def _revert_shrink(db, db_slice: Slice, s_json: dict) -> None:
    logger.warning("[STUCK-OPS] ✂️  Eliminación del slice %s sin respuesta tras %.0f min — revirtiendo.",
                   db_slice.id, STUCK_OP_TIMEOUT_MINUTES)
    s_json.pop("pending_shrink", None)
    db_slice.slice_json = dict(s_json)
    db_slice.status = "ACTIVE"
    db.commit()
    audit("system", "system", "StuckOpsWatchdog", "slice_shrink_timeout",
          f"Slice '{db_slice.name}': eliminación revertida por falta de respuesta.",
          level="WARNING", slice_id=db_slice.id, project_id=db_slice.project_id)
    await notification_hub.notify_user(db_slice.creator_id, {
        "type": "slice_failed", "slice_id": db_slice.id,
        "title": "Eliminación revertida",
        "message": f"\"{db_slice.name}\" no recibió confirmación a tiempo — el slice sigue como estaba.",
    })


async def _check_stuck_operations() -> None:
    db = SessionLocal()
    try:
        candidates = db.query(Slice).filter(Slice.status == "PROVISIONING").all()
        now = datetime.utcnow()

        for sl in candidates:
            s_json = sl.slice_json or {}
            if isinstance(s_json, str):
                s_json = json.loads(s_json)

            pending_ext = s_json.get("pending_extension")
            pending_shr = s_json.get("pending_shrink")
            pending = pending_ext or pending_shr
            if not pending:
                continue  # PROVISIONING por un deploy inicial normal, no por un modify

            requested_at = _parse_date(pending.get("requested_at"))
            if requested_at is None:
                logger.warning("[STUCK-OPS] Slice %s: operación pendiente sin 'requested_at' legible — se omite.",
                               sl.id)
                continue

            if now - requested_at < timedelta(minutes=STUCK_OP_TIMEOUT_MINUTES):
                continue

            try:
                if pending_ext:
                    await _revert_extension(db, sl, pending_ext)
                else:
                    await _revert_shrink(db, sl, s_json)
            except Exception as exc:
                logger.error("[STUCK-OPS] Error revirtiendo slice %s: %s", sl.id, exc, exc_info=True)
                db.rollback()
    finally:
        db.close()


async def stuck_ops_scheduler_task():
    logger.info("[STUCK-OPS] Vigía iniciado. Intervalo: %ds, timeout: %.0f min.",
                STUCK_OP_CHECK_INTERVAL_SECONDS, STUCK_OP_TIMEOUT_MINUTES)
    while True:
        await asyncio.sleep(STUCK_OP_CHECK_INTERVAL_SECONDS)
        try:
            await _check_stuck_operations()
        except Exception as exc:
            logger.error("[STUCK-OPS] Error en scheduler: %s", exc, exc_info=True)
