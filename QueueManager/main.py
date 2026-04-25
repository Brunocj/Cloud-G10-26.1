"""Punto de entrada del Queue Manager."""

import asyncio
import signal

from app.core.logging_config import setup_logging

setup_logging()

import logging
from app.api.health import start_health_server
from app.core.config import settings
from app.services.nats_client import nats_manager
from app.services.handlers import handle_deploy, handle_destroy

logger = logging.getLogger(__name__)


async def main():
    await nats_manager.connect()

    await nats_manager.subscribe(settings.SUBJECT_DEPLOY,  handle_deploy)
    await nats_manager.subscribe(settings.SUBJECT_DESTROY, handle_destroy)

    logger.info(
        f"Queue Manager iniciado. "
        f"Escuchando en '{settings.SUBJECT_DEPLOY}' y '{settings.SUBJECT_DESTROY}'"
    )

    stop = asyncio.Event()

    def _shutdown(signum, frame):
        logger.info(f"Señal {signum} recibida, deteniendo...")
        stop.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT,  _shutdown)

    await stop.wait()
    await nats_manager.disconnect()


if __name__ == "__main__":
    start_health_server()
    asyncio.run(main())
