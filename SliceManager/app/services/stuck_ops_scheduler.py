# app/services/stuck_ops_scheduler.py
"""
Vigía de operaciones asíncronas que se quedan colgadas esperando una
confirmación del QueueManager que nunca llega.

1. Ediciones en caliente (modify) — slices en PROVISIONING
   Si QueueManager (o cualquier paso del saga: Placement/Network/Compute) nunca
   responde a un extend/shrink, el slice queda en PROVISIONING para siempre y
   las VMs nuevas (state=DRAFT) retienen su vnc_port indefinidamente — con solo
   99 puertos por worker, esto agota el pool. Además, mientras `pending_extension`
   o `pending_shrink` sigan seteados, `modify_active_slice` bloquea cualquier
   nuevo intento de edición sobre ese slice (ver deploy_router.py).

2. Destrucciones — slices en TERMINATING
   `destroy_deployed_slice` marca TERMINATING y deja `pending_destroy`; el único
   que saca al slice de ahí es `nats_listener` al confirmar la limpieza física.
   Si esa confirmación se pierde (QM caído a mitad de la saga, payload inválido,
   resultado publicado con el SliceManager abajo), el slice queda TERMINATING
   para siempre y ni el DELETE ni el Kill Switch lo aceptan de vuelta.
   Acá se reintenta la orden hasta STUCK_DESTROY_MAX_ATTEMPTS veces y, si aun
   así no hay respuesta, se devuelve el slice a su estado previo para que el
   dueño pueda reintentar.

En NINGÚN caso se liberan VLANs/IPs ni se marca TERMINATED por timeout: sin
confirmación real no sabemos si la infraestructura sigue viva en un worker, y
liberar una VLAN todavía configurada permite que otro slice la reutilice y
colisione en Capa 2 (ver slice_destroyer.py).

Este scheduler revisa todo cada STUCK_OP_CHECK_INTERVAL_SECONDS y solo actúa
sobre operaciones más viejas que STUCK_OP_TIMEOUT_MINUTES.
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
STUCK_DESTROY_MAX_ATTEMPTS: int = int(os.getenv("STUCK_DESTROY_MAX_ATTEMPTS", "3"))

_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")


def _parse_date(raw):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except (ValueError, TypeError):
            continue
    return None


def destroy_is_stale(s_json) -> bool:
    """
    True si el slice tiene un `pending_destroy` que ya superó el timeout.

    Lo usa `force_destroy` (deploy_router) para permitir que un admin reintente
    la destrucción de un slice atascado en TERMINATING sin esperar al watchdog,
    en vez de rebotar con 409 y dejarlo sin salida.
    """
    if isinstance(s_json, str):
        try:
            s_json = json.loads(s_json)
        except (ValueError, TypeError):
            return True  # slice_json ilegible: no podemos saber si sigue en curso
    pending = (s_json or {}).get("pending_destroy")
    if not pending:
        # TERMINATING sin pending_destroy: estado inconsistente, también atascado.
        return True
    requested_at = _parse_date(pending.get("requested_at"))
    if requested_at is None:
        return True
    return datetime.utcnow() - requested_at >= timedelta(minutes=STUCK_OP_TIMEOUT_MINUTES)


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


async def _retry_or_revert_destroy(db, db_slice: Slice, s_json: dict, pending_destroy: dict) -> None:
    """
    Reintenta la orden de destroy; agotados los intentos, devuelve el slice a su
    estado previo. Nunca libera VLANs/IPs ni marca TERMINATED sin confirmación.
    """
    from app.services.slice_destroyer import destroy_deployed_slice

    attempts = int(pending_destroy.get("attempts") or 1)
    if attempts < STUCK_DESTROY_MAX_ATTEMPTS:
        logger.warning("[STUCK-OPS] 💣 Destrucción del slice %s sin respuesta tras %.0f min "
                       "(intento %d/%d) — republicando la orden.",
                       db_slice.id, STUCK_OP_TIMEOUT_MINUTES, attempts, STUCK_DESTROY_MAX_ATTEMPTS)
        # Reentrante: conserva previous_status y hace attempts += 1 (ver slice_destroyer).
        if await destroy_deployed_slice(db, db_slice):
            audit("system", "system", "StuckOpsWatchdog", "destroy_retried",
                  f"Slice '{db_slice.name}': destrucción reintentada "
                  f"({attempts + 1}/{STUCK_DESTROY_MAX_ATTEMPTS}) por falta de respuesta.",
                  level="WARNING", slice_id=db_slice.id, project_id=db_slice.project_id)
            return
        logger.error("[STUCK-OPS] No se pudo republicar el destroy del slice %s (NATS caído) — "
                     "se revierte para que el usuario reintente.", db_slice.id)

    prev = pending_destroy.get("previous_status") or "ACTIVE"
    logger.error("[STUCK-OPS] 💣 Destrucción del slice %s abandonada tras %d intento(s) — "
                 "volviendo a %s. Los recursos NO se liberaron: pueden seguir vivos en el worker.",
                 db_slice.id, attempts, prev)
    s_json.pop("pending_destroy", None)
    db_slice.slice_json = dict(s_json)
    db_slice.status = prev
    db.commit()
    audit("system", "system", "StuckOpsWatchdog", "destroy_timeout",
          f"Slice '{db_slice.name}': la destrucción no recibió confirmación tras "
          f"{attempts} intento(s). El slice vuelve a {prev} — puede haber infraestructura "
          "huérfana, revisar el worker.",
          level="ERROR", slice_id=db_slice.id, project_id=db_slice.project_id)
    await notification_hub.notify_user(db_slice.creator_id, {
        "type": "slice_failed", "slice_id": db_slice.id,
        "title": "Destrucción fallida",
        "message": f"No se pudo destruir \"{db_slice.name}\" — puedes reintentar.",
    })


async def _check_stuck_modifies(db, now) -> None:
    for sl in db.query(Slice).filter(Slice.status == "PROVISIONING").all():
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


async def _check_stuck_destroys(db, now) -> None:
    for sl in db.query(Slice).filter(Slice.status == "TERMINATING").all():
        s_json = sl.slice_json or {}
        if isinstance(s_json, str):
            s_json = json.loads(s_json)

        pending_destroy = s_json.get("pending_destroy")
        if not pending_destroy:
            # TERMINATING sin pending_destroy: quedó de una versión anterior o de
            # un commit a medias. Lo sembramos ANTES de reintentar — si no,
            # `destroy_deployed_slice` tomaría el status actual como previous_status
            # y guardaría previous_status="TERMINATING", dejando al slice sin ningún
            # estado sano al que volver cuando se agoten los reintentos.
            logger.warning("[STUCK-OPS] Slice %s en TERMINATING sin 'pending_destroy' — "
                           "se reintenta la destrucción.", sl.id)
            pending_destroy = {"previous_status": "ACTIVE", "attempts": 0}
            s_json["pending_destroy"] = pending_destroy
            sl.slice_json = dict(s_json)
            db.commit()
        else:
            requested_at = _parse_date(pending_destroy.get("requested_at"))
            if requested_at is not None and now - requested_at < timedelta(minutes=STUCK_OP_TIMEOUT_MINUTES):
                continue

        try:
            await _retry_or_revert_destroy(db, sl, s_json, pending_destroy)
        except Exception as exc:
            logger.error("[STUCK-OPS] Error procesando el destroy colgado del slice %s: %s",
                         sl.id, exc, exc_info=True)
            db.rollback()


async def _check_stuck_operations() -> None:
    db = SessionLocal()
    try:
        now = datetime.utcnow()
        await _check_stuck_modifies(db, now)
        await _check_stuck_destroys(db, now)
    finally:
        db.close()


async def stuck_ops_scheduler_task():
    logger.info("[STUCK-OPS] Vigía iniciado. Intervalo: %ds, timeout: %.0f min, "
                "reintentos de destroy: %d.",
                STUCK_OP_CHECK_INTERVAL_SECONDS, STUCK_OP_TIMEOUT_MINUTES,
                STUCK_DESTROY_MAX_ATTEMPTS)
    while True:
        await asyncio.sleep(STUCK_OP_CHECK_INTERVAL_SECONDS)
        try:
            await _check_stuck_operations()
        except Exception as exc:
            logger.error("[STUCK-OPS] Error en scheduler: %s", exc, exc_info=True)
