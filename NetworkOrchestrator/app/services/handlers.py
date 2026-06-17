"""
Handlers de mensajes NATS.
Validan los JSON de entrada y gatillan la configuración de red física.
"""

import json
import logging
import asyncio
from nats.aio.msg import Msg

from app.models.schemas import DeployNetworkRequest, DestroyNetworkRequest
from app.services.provisioner import NetworkProvisioner
from app.services.openstack_network_executor import OpenStackNetworkExecutor
from app.services.queue_client import queue_client

logger = logging.getLogger(__name__)

# Instanciamos la clase que contiene la lógica de red (La crearemos en la Parte 3)
_provisioner = NetworkProvisioner()

async def handle_deploy(msg: Msg) -> None:
    payload = {}
    try:
        payload = json.loads(msg.data.decode())
        logger.info(f"[deploy] Mensaje recibido: slice={payload.get('slice_id')}")

        # Pydantic valida que la lista de enlaces y VLANs venga correcta
        request = DeployNetworkRequest(**payload)

        # Bifurcación Strategy Pattern por availability_zone_id
        if request.availability_zone_id == 2:
            logger.info(f"[deploy] Strategy: OpenStack (slice_id={request.slice_id})")
            os_executor = OpenStackNetworkExecutor()
            response = await os_executor.deploy(request)
        else:
            logger.info(f"[deploy] Strategy: Linux Cluster (slice_id={request.slice_id})")
            # Ejecutamos la configuración de switches en un hilo aparte para no bloquear asynico
            response = await asyncio.get_running_loop().run_in_executor(
                None, _provisioner.deploy, request
            )
        logger.info(f"[deploy] Configuración de red terminada: status={response.status}")

        if msg.reply:
            await queue_client.reply(msg.reply, response.model_dump())
        else:
            logger.warning("El mensaje de NATS no solicitó respuesta (msg.reply vacío)")

    except Exception as exc:
        logger.error(f"[deploy] Error crítico: {exc}", exc_info=True)
        if msg.reply:
            await queue_client.reply(msg.reply, {
                "status": "error",
                "slice_id": payload.get("slice_id", ""),
                "request_id": payload.get("request_id", ""),
                "links_ok": [],
                "links_failed": [],
            })

async def handle_destroy(msg: Msg) -> None:
    # Lógica idéntica para destruir redes (borrar VLANs y puertos OVS)
    payload = {}
    try:
        payload = json.loads(msg.data.decode())
        logger.info(f"[destroy] Mensaje recibido: slice={payload.get('slice_id')}")

        request = DestroyNetworkRequest(**payload)

        # Bifurcación Strategy Pattern por availability_zone_id
        if request.availability_zone_id == 2:
            logger.info(f"[destroy] Strategy: OpenStack (slice_id={request.slice_id})")
            os_executor = OpenStackNetworkExecutor()
            response = await os_executor.destroy(request)
        else:
            logger.info(f"[destroy] Strategy: Linux Cluster (slice_id={request.slice_id})")
            response = await asyncio.get_running_loop().run_in_executor(
                None, _provisioner.destroy, request
            )

        if msg.reply:
            await queue_client.reply(msg.reply, response.model_dump())

    except Exception as exc:
        logger.error(f"[destroy] Error: {exc}", exc_info=True)
        if msg.reply:
            await queue_client.reply(msg.reply, {
                "status": "error",
                "slice_id": payload.get("slice_id", ""),
                "request_id": payload.get("request_id", ""),
                "error": str(exc)
            })