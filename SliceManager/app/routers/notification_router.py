# app/routers/notification_router.py
"""
Endpoint WebSocket de notificaciones.

El API Gateway valida el JWT del navegador y reenvía la conexión aquí con la
identidad en query params (user_id, role). Igual que los headers X-User-*,
estos parámetros son de confianza porque solo el Gateway puede alcanzar este
servicio dentro de la red Docker.
"""
import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.auth import ROLES
from app.services.notification_hub import notification_hub

logger = logging.getLogger("SliceManager.notify")

router = APIRouter(tags=["Notifications"])


@router.websocket("/api/v1/notifications/ws")
async def notifications_ws(websocket: WebSocket):
    user_id = websocket.query_params.get("user_id")
    role    = websocket.query_params.get("role")

    if not user_id or role not in ROLES:
        await websocket.close(code=1008, reason="Identidad inválida")
        return

    await notification_hub.connect(websocket, user_id, role)
    try:
        # Mantener viva la conexión; el cliente puede mandar pings de texto.
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("[WS] Conexión cerrada con error: %s", exc)
    finally:
        await notification_hub.disconnect(websocket, user_id, role)
