"""
Handlers de mensajes NATS.
Cada handler corresponde a un subject al que el Queue Manager está suscrito.
Deserializa el mensaje y lo pasa al Orchestrator.
"""

import json
import logging

from nats.aio.msg import Msg

from app.models.schemas import DeploySliceRequest, DestroySliceRequest
from app.services.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

_orchestrator = Orchestrator()


async def handle_deploy(msg: Msg) -> None:
    """
    Handler para slice.deploy
    Publicado por el Slice Manager cuando quiere desplegar un slice.
    """
    await msg.ack()
    try:
        payload = json.loads(msg.data.decode())
        request = DeploySliceRequest(**payload)
        logger.info("="*70)
        logger.info("[QM] 📥 Mensaje NATS recibido en 'slice.deploy'")
        logger.info("[QM]    slice_id=%s  request_id=%s  VMs=%d  links=%d",
                    request.slice_id, request.request_id,
                    len(request.vms), len(request.links))
        await _orchestrator.deploy(request)
    except Exception as exc:
        logger.error("[QM] ❌ Error procesando slice.deploy: %s", exc, exc_info=True)


async def handle_destroy(msg: Msg) -> None:
    """
    Handler para slice.destroy
    Publicado por el Slice Manager cuando quiere destruir un slice.
    """
    await msg.ack()
    try:
        payload = json.loads(msg.data.decode())
        request = DestroySliceRequest(**payload)
        logger.info("[QM] 📥 Mensaje NATS recibido en 'slice.destroy'")
        logger.info("[QM]    slice_id=%s  request_id=%s", request.slice_id, request.request_id)
        await _orchestrator.destroy(request)
    except Exception as exc:
        logger.error("[QM] ❌ Error procesando slice.destroy: %s", exc, exc_info=True)
