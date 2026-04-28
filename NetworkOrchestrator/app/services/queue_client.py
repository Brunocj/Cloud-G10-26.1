"""
Cliente NATS para el Network Orchestrator.
Escucha requests en network.deploy y network.destroy y responde directamente.
"""

import json
import logging
from typing import Optional
import nats
from nats.aio.client import Client as NATSClient

from app.core.config import settings

logger = logging.getLogger(__name__)

class NATSQueueClient:
    def __init__(self):
        self._nc: Optional[NATSClient] = None

    async def connect(self) -> None:
        self._nc = await nats.connect(
            settings.NATS_URL,
            name=settings.SERVICE_NAME,
            reconnect_time_wait=2,
            max_reconnect_attempts=-1,
        )
        logger.info(f"Conectado a NATS: {settings.NATS_URL}")

    async def disconnect(self) -> None:
        if self._nc:
            await self._nc.drain()

    # ── Suscripción a peticiones del Queue Manager ────────────────────────────
    async def subscribe_deploy(self, handler) -> None:
        await self._nc.subscribe(settings.QUEUE_DEPLOY, cb=handler)
        logger.info(f"Suscrito a '{settings.QUEUE_DEPLOY}'")

    async def subscribe_destroy(self, handler) -> None:
        await self._nc.subscribe(settings.QUEUE_DESTROY, cb=handler)
        logger.info(f"Suscrito a '{settings.QUEUE_DESTROY}'")

    # ── Respuesta directa al Queue Manager ────────────────────────────────────
    async def reply(self, reply_subject: str, payload: dict) -> None:
        data = json.dumps(payload).encode()
        await self._nc.publish(reply_subject, data)
        logger.debug(f"Respuesta enviada a '{reply_subject}'")

    def is_connected(self) -> bool:
        return self._nc is not None and self._nc.is_connected

queue_client = NATSQueueClient()