"""
Worker principal del Compute Provisioner.
Escucha requests NATS del Queue Manager y responde directamente.
"""

import asyncio
import json
import logging
import signal

from nats.aio.msg import Msg

from app.core.config import settings
from app.models.schemas import DeploySliceRequest, DestroySliceRequest
from app.services.provisioner import ComputeProvisioner
from app.services.queue_client import queue_client

logger = logging.getLogger(__name__)

_provisioner = ComputeProvisioner()


async def handle_deploy(msg: Msg) -> None:
    payload = {}
    try:
        payload = json.loads(msg.data.decode())
        logger.info(f"[deploy] Mensaje recibido: slice={payload.get('slice_id')} reply={msg.reply}")

        request = DeploySliceRequest(**payload)

        response = await asyncio.get_running_loop().run_in_executor(
            None, _provisioner.deploy, request
        )
        logger.info(f"[deploy] Provisioner terminó: status={response.status}")

        if response.vms:
            specs_by_id = {vm.vm_id: vm for vm in request.vms}
            records = [
                {
                    "vm_id":           vm.vm_id,
                    "worker_ip":       vm.worker_ip,
                    "pid":             vm.pid,
                    "vnc_port":        vm.vnc_port,
                    "ssh_user":        specs_by_id[vm.vm_id].ssh_user,
                    "ssh_private_key": specs_by_id[vm.vm_id].ssh_private_key,
                }
                for vm in response.vms
            ]
            await queue_client.save_slice_vms(request.slice_id, records)
            logger.info(f"[deploy] Estado guardado en KV para slice={request.slice_id}")

        if msg.reply:
            await queue_client.reply(msg.reply, response.model_dump())
            logger.info(f"[deploy] Reply enviado a {msg.reply}")
        else:
            logger.warning("[deploy] msg.reply vacío — Queue Manager no recibirá respuesta")

    except Exception as exc:
        logger.error(f"[deploy] Error: {exc}", exc_info=True)
        if msg.reply:
            await queue_client.reply(msg.reply, {
                "status": "error",
                "slice_id": payload.get("slice_id", ""),
                "request_id": payload.get("request_id", ""),
                "vms": [],
                "failed_vms": [],
            })


async def handle_destroy(msg: Msg) -> None:
    payload = {}
    try:
        payload = json.loads(msg.data.decode())
        logger.info(f"[destroy] Mensaje recibido: slice={payload.get('slice_id')} reply={msg.reply}")

        request = DestroySliceRequest(**payload)

        vm_records = await queue_client.get_slice_vms(request.slice_id) or []
        logger.info(f"[destroy] VMs en KV: {len(vm_records)}")

        response = await asyncio.get_running_loop().run_in_executor(
            None, _provisioner.destroy, request, vm_records
        )
        logger.info(f"[destroy] Provisioner terminó: status={response.status}")

        await queue_client.delete_slice_vms(request.slice_id)

        if msg.reply:
            await queue_client.reply(msg.reply, response.model_dump())
            logger.info(f"[destroy] Reply enviado a {msg.reply}")
        else:
            logger.warning("[destroy] msg.reply vacío — Queue Manager no recibirá respuesta")

    except Exception as exc:
        logger.error(f"[destroy] Error: {exc}", exc_info=True)
        if msg.reply:
            await queue_client.reply(msg.reply, {
                "status": "error",
                "slice_id": payload.get("slice_id", ""),
                "request_id": payload.get("request_id", ""),
                "destroyed_vms": [],
                "failed_vms": [],
            })


async def run_worker():
    await queue_client.connect()
    await queue_client.subscribe_deploy(handle_deploy)
    await queue_client.subscribe_destroy(handle_destroy)

    logger.info(
        f"Compute Provisioner iniciado. "
        f"Escuchando en '{settings.QUEUE_DEPLOY}' y '{settings.QUEUE_DESTROY}'"
    )

    stop = asyncio.Event()

    def _shutdown(signum, frame):
        logger.info(f"Señal {signum} recibida, deteniendo...")
        stop.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)

    await stop.wait()
    await queue_client.disconnect()
