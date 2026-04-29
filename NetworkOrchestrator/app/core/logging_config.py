"""Configuración de logging estructurado."""

import logging
import sys
from app.core.config import settings

def setup_logging():
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    fmt   = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

    logging.basicConfig(
        level=level,
        format=fmt,
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # Silenciar logs verbosos de paramiko (conexiones SSH)
    logging.getLogger("paramiko").setLevel(logging.WARNING)