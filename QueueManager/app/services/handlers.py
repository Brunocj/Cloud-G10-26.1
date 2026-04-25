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
        await _orchestrator.deploy(request)
    except Exception as exc:
        logger.error(f"Error procesando slice.deploy: {exc}", exc_info=True)


async def handle_destroy(msg: Msg) -> None:
    """
    Handler para slice.destroy
    Publicado por el Slice Manager cuando quiere destruir un slice.
    """
    await msg.ack()
    try:
        payload = json.loads(msg.data.decode())
        request = DestroySliceRequest(**payload)
        await _orchestrator.destroy(request)
    except Exception as exc:
        logger.error(f"Error procesando slice.destroy: {exc}", exc_info=True)
