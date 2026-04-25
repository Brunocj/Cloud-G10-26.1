"""
Cliente NATS para el Compute Provisioner.
Reemplaza al cliente Redis anterior.

El Compute Provisioner actúa como servidor de requests NATS:
- Escucha requests en compute.deploy y compute.destroy
- Responde directamente al Queue Manager en el mismo request
- Persiste el estado de VMs en NATS KV para el destroy
"""

import json
import logging
from typing import Optional, List

import nats
from nats.aio.client import Client as NATSClient
from nats.js import JetStreamContext
from nats.js.kv import KeyValue

from app.core.config import settings

logger = logging.getLogger(__name__)

_SLICE_VMS_KEY = "compute-vms:{slice_id}"


class NATSQueueClient:

    def __init__(self):
        self._nc: Optional[NATSClient]       = None
        self._js: Optional[JetStreamContext]  = None
        self._kv: Optional[KeyValue]          = None

    async def connect(self) -> None:
        self._nc = await nats.connect(
            settings.NATS_URL,
            name=settings.SERVICE_NAME,
            reconnect_time_wait=2,
            max_reconnect_attempts=-1,
        )
        self._js = self._nc.jetstream()
        await self._setup_kv()
        logger.info(f"Conectado a NATS: {settings.NATS_URL}")

    async def disconnect(self) -> None:
        if self._nc:
            await self._nc.drain()

    async def _setup_kv(self) -> None:
        try:
            self._kv = await self._js.key_value(settings.NATS_KV_BUCKET)
        except Exception:
            self._kv = await self._js.create_key_value(
                bucket=settings.NATS_KV_BUCKET,
                ttl=3600,
            )
            logger.info(f"KV bucket '{settings.NATS_KV_BUCKET}' creado")

    # ── Suscripción a requests ────────────────────────────────────────────────

    async def subscribe_deploy(self, handler) -> None:
        """Suscribe al subject compute.deploy para recibir requests del Queue Manager."""
        await self._nc.subscribe(settings.QUEUE_DEPLOY, cb=handler)
        logger.info(f"Suscrito a '{settings.QUEUE_DEPLOY}'")

    async def subscribe_destroy(self, handler) -> None:
        """Suscribe al subject compute.destroy para recibir requests del Queue Manager."""
        await self._nc.subscribe(settings.QUEUE_DESTROY, cb=handler)
        logger.info(f"Suscrito a '{settings.QUEUE_DESTROY}'")

    # ── Respuesta al Queue Manager ────────────────────────────────────────────

    async def reply(self, reply_subject: str, payload: dict) -> None:
        """Responde al Queue Manager con el resultado de la operación."""
        data = json.dumps(payload).encode()
        await self._nc.publish(reply_subject, data)
        logger.debug(f"Respuesta enviada a '{reply_subject}'")

    # ── Persistencia de estado en KV ──────────────────────────────────────────

    async def save_slice_vms(self, slice_id: str, vms: List[dict]) -> None:
        """Persiste las VMs desplegadas para poder destruirlas luego."""
        key = _SLICE_VMS_KEY.format(slice_id=slice_id)
        await self._kv.put(key, json.dumps(vms).encode())

    async def get_slice_vms(self, slice_id: str) -> Optional[List[dict]]:
        """Recupera las VMs desplegadas de un slice."""
        try:
            key = _SLICE_VMS_KEY.format(slice_id=slice_id)
            entry = await self._kv.get(key)
            return json.loads(entry.value.decode())
        except Exception:
            return None

    async def delete_slice_vms(self, slice_id: str) -> None:
        """Elimina el estado de VMs de un slice."""
        try:
            key = _SLICE_VMS_KEY.format(slice_id=slice_id)
            await self._kv.delete(key)
        except Exception:
            pass

    def is_connected(self) -> bool:
        return self._nc is not None and self._nc.is_connected


# Instancia global
queue_client = NATSQueueClient()
