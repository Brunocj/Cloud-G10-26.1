# app/services/ttl_scheduler.py
"""
TTL Scheduler — auto-destrucción de slices vencidos.

Cada TTL_CHECK_INTERVAL_SECONDS revisa los slices ACTIVE cuyo
date_deployed + TTL(horas) ya pasó, y dispara la misma orden de
destrucción que el DELETE manual (via slice_destroyer).

Un TTL nulo o <= 0 significa "sin expiración" (persistente).
Notifica al dueño por WebSocket cuando su slice es destruido.
"""
import asyncio
import logging
import os
from datetime import datetime, timedelta

from app.database import SessionLocal
from app.models import Slice
from app.services.notification_hub import notification_hub
from app.services.slice_destroyer import destroy_deployed_slice
from app.services.audit import audit

logger = logging.getLogger("SliceManager.TTL")

TTL_CHECK_INTERVAL_SECONDS: int = int(os.getenv("TTL_CHECK_INTERVAL_SECONDS", "60"))

_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


def _parse_date(raw: str):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except (ValueError, TypeError):
            continue
    return None


async def _check_expired_slices() -> None:
    db = SessionLocal()
    try:
        candidates = db.query(Slice).filter(
            Slice.status == "ACTIVE",
            Slice.TTL.isnot(None),
            Slice.date_deployed.isnot(None),
        ).all()

        now = datetime.utcnow()
        for sl in candidates:
            ttl_hours = float(sl.TTL or 0)
            if ttl_hours <= 0:
                continue  # persistente / sin expiración

            deployed_at = _parse_date(sl.date_deployed)
            if deployed_at is None:
                logger.warning("[TTL] Slice %s: date_deployed ilegible ('%s') — se omite",
                               sl.id, sl.date_deployed)
                continue

            expires_at = deployed_at + timedelta(hours=ttl_hours)
            if now < expires_at:
                continue

            logger.info("[TTL] ⏰ Slice %s ('%s') venció (deployed=%s ttl=%.1fh). Destruyendo…",
                        sl.id, sl.name, sl.date_deployed, ttl_hours)
            try:
                ok = await destroy_deployed_slice(db, sl)
                if ok:
                    logger.info("[TTL] 🧹 Slice %s destruido automáticamente.", sl.id)
                    audit("system", "system", "TTL", "slice_expired",
                          f"Slice '{sl.name}' destruido automáticamente al vencer su TTL de {ttl_hours:g}h.",
                          slice_id=sl.id, project_id=sl.project_id)
                    await notification_hub.notify_user(sl.creator_id, {
                        "type":     "slice_expired",
                        "slice_id": sl.id,
                        "title":    "Slice destruido por TTL",
                        "message":  f"Tu slice \"{sl.name}\" alcanzó su tiempo de vida "
                                    f"({ttl_hours:g}h) y fue destruido automáticamente.",
                    })
                else:
                    logger.error("[TTL] ❌ No se pudo publicar destroy para slice %s "
                                 "(NATS). Se reintentará en el próximo ciclo.", sl.id)
            except Exception as exc:
                logger.error("[TTL] Error destruyendo slice %s: %s", sl.id, exc, exc_info=True)
                db.rollback()
    finally:
        db.close()


async def ttl_scheduler_task():
    logger.info("[TTL] Scheduler iniciado. Intervalo: %ds.", TTL_CHECK_INTERVAL_SECONDS)
    while True:
        await asyncio.sleep(TTL_CHECK_INTERVAL_SECONDS)
        try:
            await _check_expired_slices()
        except Exception as exc:
            logger.error("[TTL] Error en scheduler: %s", exc, exc_info=True)
