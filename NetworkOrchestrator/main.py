"""Punto de entrada del Network Orchestrator."""

import asyncio
import signal
import logging

from app.core.logging_config import setup_logging
setup_logging()

from app.api.health import start_health_server
from app.core.config import settings
from app.services.queue_client import queue_client
from app.services.handlers import handle_deploy, handle_destroy

logger = logging.getLogger(__name__)

async def main():
    await queue_client.connect()
    await queue_client.subscribe_deploy(handle_deploy)
    await queue_client.subscribe_destroy(handle_destroy)

    logger.info(
        f"Network Orchestrator iniciado. "
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

if __name__ == "__main__":
    start_health_server()
    asyncio.run(main())