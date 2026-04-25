"""Punto de entrada del Compute Provisioner."""

import asyncio
from app.core.logging_config import setup_logging

setup_logging()

from app.api.health import start_health_server
from app.core.worker import run_worker

if __name__ == "__main__":
    start_health_server()
    asyncio.run(run_worker())
