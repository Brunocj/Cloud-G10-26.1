"""
Configuración centralizada del Compute Provisioner.
Los valores se leen desde variables de entorno (con defaults razonables).
"""

from pydantic_settings import BaseSettings
from typing import List


class Settings(BaseSettings):

    # ── Identificación del servicio ─────────────────────────────────────────
    SERVICE_NAME: str = "compute-provisioner"
    LOG_LEVEL:    str = "INFO"

    # ── Redis / Cola de mensajes ─────────────────────────────────────────────
    REDIS_HOST:     str = "redis"
    REDIS_PORT:     int = 6379
    REDIS_DB:       int = 0

    # Topics de entrada (el encolador publica aquí)
    QUEUE_DEPLOY:  str = "queue:compute:deploy"
    QUEUE_DESTROY: str = "queue:compute:destroy"

    # Topics de salida (el Compute Provisioner publica resultados aquí)
    QUEUE_RESULT:  str = "queue:compute:result"

    # ── SSH hacia los workers ────────────────────────────────────────────────
    SSH_USER:        str  = "root"
    SSH_KEY_PATH:    str  = "/root/.ssh/id_rsa"
    SSH_TIMEOUT:     int  = 30    # segundos por conexión
    SSH_MAX_RETRIES: int  = 3     # reintentos ante fallo de una VM
    SSH_RETRY_DELAY: int  = 5     # segundos entre reintentos

    # ── Rutas en los workers ─────────────────────────────────────────────────
    IMAGES_BASE_DIR: str = "/images"   # donde están las imágenes base
    VMS_BASE_DIR:    str = "/vms"      # donde se crean los discos thin

    # ── Concurrencia ─────────────────────────────────────────────────────────
    MAX_CONCURRENT_WORKERS: int = 10   # VMs desplegadas en paralelo

    # ── Healthcheck ──────────────────────────────────────────────────────────
    HEALTH_PORT: int = 8080

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
