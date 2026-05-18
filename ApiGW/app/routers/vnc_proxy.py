# app/routers/vnc_proxy.py
"""
Proxy WebSocket para consola VNC.

El browser no puede alcanzar 10.0.10.x directamente.
Este proxy recibe la conexion WS del browser y la tuneliza
hacia el worker real.

URL:  ws://localhost:8085/vnc/{worker_ip}/{ws_port}
"""
import logging

import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("api-gateway.vnc")

router = APIRouter(tags=["VNC Proxy"])


@router.websocket("/vnc/{worker_ip}/{ws_port}")
async def vnc_proxy(websocket: WebSocket, worker_ip: str, ws_port: int):
    """
    Proxy bidireccional entre el browser y el WebSocket VNC del worker.
    """
    await websocket.accept()
    target_url = f"ws://{worker_ip}:{ws_port}"
    logger.info("VNC proxy: browser → %s", target_url)

    try:
        async with websockets.connect(
            target_url,
            subprotocols=["binary", "base64"],
            ping_interval=None,          # QEMU maneja sus propios keep-alives
            open_timeout=10,
        ) as upstream:
            import asyncio

            async def browser_to_worker():
                try:
                    while True:
                        data = await websocket.receive_bytes()
                        await upstream.send(data)
                except (WebSocketDisconnect, Exception):
                    pass

            async def worker_to_browser():
                try:
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)
                except (WebSocketDisconnect, Exception):
                    pass

            # Ejecutamos ambas direcciones concurrentemente
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(browser_to_worker()),
                    asyncio.create_task(worker_to_browser()),
                ],
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()

    except Exception as exc:
        logger.warning("VNC proxy error (%s): %s", target_url, exc)

    finally:
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info("VNC proxy cerrado: %s", target_url)
