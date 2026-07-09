# app/routers/notifications_ws.py
"""
Proxy WebSocket de notificaciones en tiempo real.

El navegador se conecta a:
    ws://<gateway>/notifications/ws?token=<JWT>

El gateway valida el JWT de Keycloak (los browsers no pueden mandar headers
Authorization en WebSockets), extrae la identidad (sub + rol más alto) y abre
una conexión interna hacia el hub del Slice Manager pasando la identidad como
query params — el mismo modelo de confianza que los headers X-User-*.
"""
import asyncio
import logging

import jwt
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.config import settings
from app.middleware.jwt_auth import _top_role

logger = logging.getLogger("api-gateway.notifications")
router = APIRouter(tags=["Notifications Proxy"])

# http://slice-manager:8000 → ws://slice-manager:8000
_SM_WS_BASE = settings.SLICE_MANAGER_URL.replace("http://", "ws://").replace("https://", "wss://")


async def _validate_token(websocket: WebSocket) -> tuple[str, str] | None:
    """Valida el JWT del query param y devuelve (user_id, role), o None si falla."""
    token = websocket.query_params.get("token")
    if not token:
        await websocket.close(code=1008, reason="Authentication token is required")
        return None

    if not settings.JWT_ENABLED:
        # Modo demo sin Keycloak: identidad anónima de solo lectura
        return ("demo-user", "usuario")

    jwks_client = getattr(websocket.app.state, "jwks_client", None)
    if not jwks_client:
        await websocket.close(code=1011, reason="Authentication service unavailable")
        return None

    try:
        loop = asyncio.get_running_loop()
        signing_key = await loop.run_in_executor(None, jwks_client.get_signing_key_from_jwt, token)

        decode_options = {"verify_exp": True}
        decode_kwargs = {
            "algorithms": ["RS256"],
            "issuer":     settings.JWT_ISSUER,
            "options":    decode_options,
        }
        if settings.JWT_AUDIENCE:
            decode_kwargs["audience"] = settings.JWT_AUDIENCE
        else:
            decode_options["verify_aud"] = False

        payload = jwt.decode(token, signing_key.key, **decode_kwargs)
        user_id = payload.get("sub")
        role    = _top_role(payload.get("realm_access", {}).get("roles", []))
        if not user_id or not role:
            await websocket.close(code=1008, reason="Token sin identidad válida")
            return None
        return (user_id, role)

    except jwt.ExpiredSignatureError:
        await websocket.close(code=1008, reason="Token has expired")
        return None
    except jwt.InvalidTokenError as exc:
        logger.warning("Notifications WS: token inválido: %s", exc)
        await websocket.close(code=1008, reason="Invalid token")
        return None
    except Exception as exc:
        logger.error("Notifications WS: error validando JWT: %s", exc, exc_info=True)
        await websocket.close(code=1011, reason="Auth error")
        return None


@router.websocket("/notifications/ws")
async def notifications_proxy(websocket: WebSocket):
    identity = await _validate_token(websocket)
    if identity is None:
        return
    user_id, role = identity

    await websocket.accept()
    upstream_url = f"{_SM_WS_BASE}/api/v1/notifications/ws?user_id={user_id}&role={role}"

    try:
        async with websockets.connect(upstream_url) as upstream:
            logger.info("Notifications WS abierto: user=%s… role=%s", user_id[:8], role)

            async def client_to_upstream():
                try:
                    while True:
                        msg = await websocket.receive_text()
                        await upstream.send(msg)
                except (WebSocketDisconnect, Exception):
                    pass

            async def upstream_to_client():
                try:
                    async for msg in upstream:
                        await websocket.send_text(msg if isinstance(msg, str) else msg.decode())
                except Exception:
                    pass

            t1 = asyncio.create_task(client_to_upstream())
            t2 = asyncio.create_task(upstream_to_client())
            _, pending = await asyncio.wait([t1, t2], return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()

    except Exception as exc:
        logger.error("Notifications WS: error conectando al Slice Manager: %s", exc)
    finally:
        try:
            await websocket.close()
        except Exception:
            pass
