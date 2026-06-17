"""
NATS Responder for VMPlacement — BYOS (Bring Your Own Scheduler)
================================================================

Suscribe al subject `slice.placement.process` y responde al WorkflowOrchestrator
usando el patrón request/reply de core NATS.

El servicio mantiene el endpoint HTTP /placement para compatibilidad con el
SliceManager legado (llamadas directas HTTP desde placement_worker.py).
"""

import asyncio
import json
import logging
import os

import nats
from nats.aio.client import Client as NATSClient

from app.models import PlacementRequest, PlacementResponse
from app.placement_engine import run_placement

logger = logging.getLogger("vm-placement.nats")

NATS_URL   = os.getenv("NATS_URL",   "nats://nats:4222")
SUBJECT_IN = os.getenv("SUBJECT_PLACEMENT", "slice.placement.process")


async def _handle_placement_request(msg) -> None:
    """
    Callback invocado cuando llega un request al subject slice.placement.process.
    Deserializa, ejecuta el placement y publica la respuesta en msg.reply.
    """
    try:
        raw = json.loads(msg.data.decode())
        request = PlacementRequest(**raw)

        n              = len(request.vms)
        timeout_sec    = max(float(n), 1.0)
        az_id          = request.availability_zone_id

        logger.info(
            "[NATS][%s] Placement request recibido — az_id=%d vms=%d",
            request.slice_id, az_id, n,
        )

        loop   = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            run_placement,
            request.vms,
            request.workers,
            timeout_sec,
            az_id,
        )
        success, placement_map, reason, detail = result

        if success:
            response = PlacementResponse(
                slice_id=request.slice_id,
                status="SUCCESS",
                placement_map=placement_map,
                reason=None,
                detail=None,
            )
            logger.info(
                "[NATS][%s] Placement SUCCESS — %d VMs asignadas.",
                request.slice_id, len(placement_map),
            )
        else:
            response = PlacementResponse(
                slice_id=request.slice_id,
                status="FAILED",
                placement_map=None,
                reason=reason,
                detail=detail,
            )
            logger.warning(
                "[NATS][%s] Placement FAILED — reason=%s detail=%s",
                request.slice_id, reason, detail,
            )

        reply_data = json.dumps(response.model_dump()).encode()
        await msg.respond(reply_data)

    except Exception as exc:
        logger.error("[NATS] Error procesando placement request: %s", exc, exc_info=True)
        error_resp = json.dumps({
            "slice_id": raw.get("slice_id", "unknown") if "raw" in dir() else "unknown",
            "status":   "FAILED",
            "reason":   "INTERNAL_ERROR",
            "detail":   str(exc),
        }).encode()
        try:
            await msg.respond(error_resp)
        except Exception:
            pass  # El reply-to puede haber expirado


class NATSPlacementResponder:
    """Gestiona la conexión NATS y la suscripción request/reply."""

    def __init__(self):
        self._nc: NATSClient | None = None

    async def connect(self) -> None:
        self._nc = await nats.connect(
            NATS_URL,
            name="vm-placement",
            reconnect_time_wait=2,
            max_reconnect_attempts=-1,
        )
        await self._nc.subscribe(SUBJECT_IN, cb=_handle_placement_request)
        logger.info("[NATS] Suscrito a '%s' en %s", SUBJECT_IN, NATS_URL)

    async def disconnect(self) -> None:
        if self._nc:
            await self._nc.drain()
            logger.info("[NATS] Conexión cerrada.")


nats_responder = NATSPlacementResponder()
