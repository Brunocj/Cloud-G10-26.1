# app/routers/vnc_proxy.py
"""
Proxy WebSocket para consola VNC — con túnel SSH.

Nueva topología: la App VM no tiene acceso directo a los workers.
El proxy abre un túnel SSH a través del gateway (10.20.11.119) en el
puerto correspondiente al worker, y desde allí hace TCP forward al
puerto WebSocket de QEMU en el worker (localhost:{ws_port}).

URL del browser:
  ws://localhost:8085/vnc/{gateway_ip}/{ssh_port}/{ws_port}

Ejemplo para server1 (puerto 5811) con display 44 (ws_port=5744):
  ws://localhost:8085/vnc/10.20.11.119/5811/5744
"""
import asyncio
import logging
import os
import select
import socket
import threading

import paramiko
import websockets
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

logger = logging.getLogger("api-gateway.vnc")
router = APIRouter(tags=["VNC Proxy"])

VNC_SSH_KEY_PATH = os.getenv("VNC_SSH_KEY_PATH", "/app/keys/id_ed25519")
VNC_SSH_USER = os.getenv("VNC_SSH_USER", "ubuntu")


# ── Helpers SSH ────────────────────────────────────────────────────────────────

def _load_ssh_key(key_path: str) -> paramiko.PKey:
    """Intenta cargar la clave SSH en formatos RSA, Ed25519 o ECDSA."""
    for key_class in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
        try:
            return key_class.from_private_key_file(key_path)
        except Exception:
            continue
    raise ValueError(f"No se pudo cargar ninguna clave SSH desde {key_path}")


def _bridge_channel_to_socket(channel: paramiko.Channel,
                               sock: socket.socket,
                               stop: threading.Event) -> None:
    """
    Thread: puente bidireccional entre un canal paramiko y un socket local.
    Termina cuando alguno de los lados cierra la conexión o stop es señalizado.
    """
    try:
        while not stop.is_set():
            rlist, _, _ = select.select([channel, sock], [], [], 1.0)
            if channel in rlist:
                data = channel.recv(65536)
                if not data:
                    break
                sock.sendall(data)
            if sock in rlist:
                data = sock.recv(65536)
                if not data:
                    break
                channel.sendall(data)
    except Exception as exc:
        logger.debug("[VNC bridge] thread terminado: %s", exc)
    finally:
        stop.set()
        for obj in (channel, sock):
            try:
                obj.close()
            except Exception:
                pass


# ── Endpoint WebSocket ─────────────────────────────────────────────────────────

@router.websocket("/vnc/{gateway_ip}/{ssh_port}/{ws_port}")
async def vnc_proxy(websocket: WebSocket,
                    gateway_ip: str,
                    ssh_port: int,
                    ws_port: int):
    """
    Proxy bidireccional:
      Browser  ←→  ApiGW WebSocket  ←→  SSH tunnel  ←→  QEMU WebSocket en worker
    """
    await websocket.accept()
    logger.info("VNC proxy via SSH: %s:%d → QEMU ws_port=%d", gateway_ip, ssh_port, ws_port)

    loop = asyncio.get_running_loop()

    # 1. Cargar clave SSH
    try:
        pkey = _load_ssh_key(VNC_SSH_KEY_PATH)
    except Exception as exc:
        logger.error("VNC: clave SSH no disponible (%s): %s", VNC_SSH_KEY_PATH, exc)
        await websocket.close(code=1011, reason="SSH key unavailable")
        return

    # 2. Conectar SSH y abrir canal TCP directo al ws_port del worker
    ssh_client = paramiko.SSHClient()
    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    channel: paramiko.Channel | None = None
    try:
        await loop.run_in_executor(
            None,
            lambda: ssh_client.connect(
                gateway_ip, port=ssh_port,
                username=VNC_SSH_USER, pkey=pkey,
                timeout=10, look_for_keys=False, allow_agent=False,
            )
        )
        channel = await loop.run_in_executor(
            None,
            lambda: ssh_client.get_transport().open_channel(
                "direct-tcpip",
                ("127.0.0.1", ws_port),   # destino en el worker
                ("127.0.0.1", 0),          # origen local (cualquier puerto)
            )
        )
    except Exception as exc:
        logger.error("VNC: SSH tunnel falló %s:%d → ws:%d: %s",
                     gateway_ip, ssh_port, ws_port, exc)
        await websocket.close(code=1011, reason="SSH tunnel failed")
        ssh_client.close()
        return

    # 3. Crear socket pair: a_sock <─────────bridge──────────> b_sock <──> SSH channel
    #    websockets se conecta a través de a_sock (que "parece" una conexión TCP)
    a_sock, b_sock = socket.socketpair()
    stop_event = threading.Event()
    threading.Thread(
        target=_bridge_channel_to_socket,
        args=(channel, b_sock, stop_event),
        daemon=True,
        name=f"vnc-bridge-{gateway_ip}-{ssh_port}-{ws_port}",
    ).start()

    # 4. Conectar websockets al servidor QEMU (a través del socket pair / SSH)
    target_url = f"ws://127.0.0.1:{ws_port}"
    try:
        async with websockets.connect(
            target_url,
            sock=a_sock,                   # la conexión real va por el socket pair
            subprotocols=["binary", "base64"],
            ping_interval=None,
            open_timeout=10,
        ) as upstream:

            async def browser_to_worker() -> None:
                try:
                    while True:
                        data = await websocket.receive_bytes()
                        await upstream.send(data)
                except (WebSocketDisconnect, Exception):
                    pass

            async def worker_to_browser() -> None:
                try:
                    async for message in upstream:
                        if isinstance(message, bytes):
                            await websocket.send_bytes(message)
                        else:
                            await websocket.send_text(message)
                except (WebSocketDisconnect, Exception):
                    pass

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
        logger.warning("VNC proxy error (%s:%d → ws:%d): %s",
                       gateway_ip, ssh_port, ws_port, exc)
    finally:
        stop_event.set()
        for obj in (a_sock, channel, ssh_client):
            try:
                obj.close()
            except Exception:
                pass
        try:
            await websocket.close()
        except Exception:
            pass
        logger.info("VNC proxy cerrado: %s:%d → ws:%d", gateway_ip, ssh_port, ws_port)
