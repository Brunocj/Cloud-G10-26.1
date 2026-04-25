"""
Cliente NATS JetStream para el Queue Manager.
"""

import json
import logging
from typing import Callable, Optional

import nats
from nats.aio.client import Client as NATSClient
from nats.js import JetStreamContext
from nats.js.api import StreamConfig, RetentionPolicy, StorageType
from nats.js.kv import KeyValue

from app.core.config import settings

logger = logging.getLogger(__name__)


class NATSManager:

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
        await self._setup_stream()
        await self._setup_kv()
        logger.info(f"Conectado a NATS: {settings.NATS_URL}")

    async def disconnect(self) -> None:
        if self._nc:
            await self._nc.drain()
            logger.info("Conexión NATS cerrada")

    # ── Stream: solo persiste mensajes slice.* ────────────────────────────────
    # compute.* usa core NATS (request/reply) — no pasa por el stream

    async def _setup_stream(self) -> None:
        try:
            # Intentar actualizar el stream si ya existe
            await self._js.update_stream(StreamConfig(
                name=settings.JS_STREAM_NAME,
                subjects=["slice.*"],
                retention=RetentionPolicy.LIMITS,
                storage=StorageType.FILE,
                max_msgs=100_000,
            ))
            logger.info(f"Stream '{settings.JS_STREAM_NAME}' actualizado")
        except Exception:
            try:
                await self._js.add_stream(StreamConfig(
                    name=settings.JS_STREAM_NAME,
                    subjects=["slice.*"],
                    retention=RetentionPolicy.LIMITS,
                    storage=StorageType.FILE,
                    max_msgs=100_000,
                ))
                logger.info(f"Stream '{settings.JS_STREAM_NAME}' creado")
            except Exception as e:
                logger.error(f"Error configurando stream: {e}")
                raise

    async def _setup_kv(self) -> None:
        try:
            self._kv = await self._js.key_value(settings.JS_KV_BUCKET)
            logger.debug(f"KV bucket '{settings.JS_KV_BUCKET}' encontrado")
        except Exception:
            self._kv = await self._js.create_key_value(
                bucket=settings.JS_KV_BUCKET,
                ttl=3600,
            )
            logger.info(f"KV bucket '{settings.JS_KV_BUCKET}' creado")

    # ── Publicación al Slice Manager (via core NATS, no JetStream) ────────────

    async def publish(self, subject: str, payload: dict) -> None:
        """Publica resultado al Slice Manager usando core NATS."""
        data = json.dumps(payload).encode()
        await self._nc.publish(subject, data)
        logger.debug(f"Publicado en '{subject}'")

    # ── Suscripción (JetStream para slice.*) ──────────────────────────────────

    async def subscribe(self, subject: str, handler: Callable,
                        durable: str = None) -> None:
        await self._js.subscribe(
            subject,
            cb=handler,
            durable=durable or subject.replace(".", "-"),
            manual_ack=True,
        )
        logger.info(f"Suscrito a '{subject}'")

    # ── Request/Reply hacia Compute Provisioner (core NATS) ───────────────────

    async def request(self, subject: str, payload: dict,
                      timeout: int = 30) -> Optional[dict]:
        """Envía request y espera respuesta directa del Compute Provisioner."""
        data = json.dumps(payload).encode()
        try:
            msg = await self._nc.request(subject, data, timeout=timeout)
            return json.loads(msg.data.decode())
        except nats.errors.TimeoutError:
            logger.warning(f"Timeout esperando respuesta de '{subject}'")
            return None

    # ── KV ────────────────────────────────────────────────────────────────────

    async def kv_put(self, key: str, value: dict) -> None:
        await self._kv.put(key, json.dumps(value).encode())

    async def kv_get(self, key: str) -> Optional[dict]:
        try:
            entry = await self._kv.get(key)
            return json.loads(entry.value.decode())
        except Exception:
            return None

    async def kv_delete(self, key: str) -> None:
        try:
            await self._kv.delete(key)
        except Exception:
            pass

    def is_connected(self) -> bool:
        return self._nc is not None and self._nc.is_connected


nats_manager = NATSManager()
