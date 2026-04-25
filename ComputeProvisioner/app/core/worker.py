"""
Worker principal del Compute Provisioner.
Consume mensajes del módulo de colas en un loop y los despacha
al provisioner correspondiente.
"""

import logging
import signal
import sys

from app.core.config import settings
from app.models.schemas import DeploySliceRequest, DestroySliceRequest
from app.services.provisioner import ComputeProvisioner
from app.services.queue_client import QueueClient

logger = logging.getLogger(__name__)

_running = True


def _handle_shutdown(signum, frame):
    global _running
    logger.info(f"Señal {signum} recibida, deteniendo worker...")
    _running = False


def run_worker():
    """Loop principal: consume mensajes y procesa de forma bloqueante."""
    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT,  _handle_shutdown)

    queue      = QueueClient()
    provisioner = ComputeProvisioner()

    logger.info(
        f"Compute Provisioner iniciado. "
        f"Escuchando en '{settings.QUEUE_DEPLOY}' y '{settings.QUEUE_DESTROY}'"
    )

    while _running:
        # Intentar consumir un mensaje de deploy
        deploy_msg = queue.consume_deploy(timeout=2)
        if deploy_msg:
            _handle_deploy(deploy_msg, provisioner, queue)
            continue

        # Intentar consumir un mensaje de destroy
        destroy_msg = queue.consume_destroy(timeout=2)
        if destroy_msg:
            _handle_destroy(destroy_msg, provisioner, queue)

    logger.info("Worker detenido.")


def _handle_deploy(msg: dict, provisioner: ComputeProvisioner, queue: QueueClient):
    try:
        request  = DeploySliceRequest(**msg)
        response = provisioner.deploy(request)
        queue.publish_deploy_result(response)
    except Exception as exc:
        logger.error(f"Error procesando deploy: {exc}", exc_info=True)


def _handle_destroy(msg: dict, provisioner: ComputeProvisioner, queue: QueueClient):
    try:
        request  = DestroySliceRequest(**msg)
        response = provisioner.destroy(request)
        queue.publish_destroy_result(response)
    except Exception as exc:
        logger.error(f"Error procesando destroy: {exc}", exc_info=True)
