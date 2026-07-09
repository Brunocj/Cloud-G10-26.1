# app/services/notification_hub.py
"""
Hub de notificaciones en tiempo real (WebSocket).

El frontend se conecta vía el API Gateway (que valida el JWT y reenvía la
identidad como query params). El hub mantiene en memoria las conexiones
activas por usuario y por rol, y expone helpers para empujar eventos:

  · notify_user(user_id, event)      → a todas las pestañas de un usuario
  · notify_users(ids, event)         → a varios usuarios
  · notify_roles(roles, event)       → a todos los conectados con esos roles
                                       (ej. avisar a admins de una solicitud)

Formato de evento (JSON):
  { "type": "...", "title": "...", "message": "...", "slice_id": ..., ... }
"""
import asyncio
import json
import logging
from collections import defaultdict
from typing import Iterable

from fastapi import WebSocket

logger = logging.getLogger("SliceManager.notify")


class NotificationHub:
    def __init__(self):
        # user_id → set de WebSockets (varias pestañas del mismo usuario)
        self._by_user: dict[str, set[WebSocket]] = defaultdict(set)
        # role → set de WebSockets
        self._by_role: dict[str, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, user_id: str, role: str) -> None:
        await ws.accept()
        async with self._lock:
            self._by_user[user_id].add(ws)
            self._by_role[role].add(ws)
        logger.info("[WS] Conectado user=%s… role=%s (total user conns=%d)",
                    user_id[:8], role, len(self._by_user[user_id]))

    async def disconnect(self, ws: WebSocket, user_id: str, role: str) -> None:
        async with self._lock:
            self._by_user[user_id].discard(ws)
            if not self._by_user[user_id]:
                del self._by_user[user_id]
            self._by_role[role].discard(ws)
        logger.info("[WS] Desconectado user=%s… role=%s", user_id[:8], role)

    async def _send(self, sockets: set[WebSocket], payload: str) -> None:
        dead = []
        for ws in list(sockets):
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(ws)
        # Limpieza perezosa de sockets muertos
        for ws in dead:
            for conns in self._by_user.values():
                conns.discard(ws)
            for conns in self._by_role.values():
                conns.discard(ws)

    async def notify_user(self, user_id: str, event: dict) -> None:
        sockets = self._by_user.get(user_id)
        if not sockets:
            return
        await self._send(set(sockets), json.dumps(event))
        logger.info("[WS] → user=%s… evento '%s'", user_id[:8], event.get("type"))

    async def notify_users(self, user_ids: Iterable[str], event: dict) -> None:
        for uid in set(user_ids):
            await self.notify_user(uid, event)

    async def notify_roles(self, roles: Iterable[str], event: dict) -> None:
        payload = json.dumps(event)
        targets: set[WebSocket] = set()
        for role in roles:
            targets |= self._by_role.get(role, set())
        if targets:
            await self._send(targets, payload)
            logger.info("[WS] → roles=%s evento '%s' (%d conexiones)",
                        list(roles), event.get("type"), len(targets))


notification_hub = NotificationHub()


def notify_user_threadsafe(loop: asyncio.AbstractEventLoop, user_id: str, event: dict) -> None:
    """Para código síncrono (schedulers en executor) que necesita notificar."""
    asyncio.run_coroutine_threadsafe(notification_hub.notify_user(user_id, event), loop)
