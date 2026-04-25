"""Configuración centralizada del Compute Provisioner."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):

    SERVICE_NAME: str = "compute-provisioner"
    LOG_LEVEL:    str = "INFO"

    # ── NATS ─────────────────────────────────────────────────────────────────
    NATS_URL:       str = "nats://nats:4222"
    NATS_KV_BUCKET: str = "compute-state"

    # Subjects de entrada (Queue Manager publica aquí)
    QUEUE_DEPLOY:  str = "compute.deploy"
    QUEUE_DESTROY: str = "compute.destroy"

    # ── SSH ───────────────────────────────────────────────────────────────────
    SSH_TIMEOUT:     int = 30
    SSH_MAX_RETRIES: int = 3
    SSH_RETRY_DELAY: int = 5

    # ── Rutas en workers ──────────────────────────────────────────────────────
    IMAGES_BASE_DIR: str = "/images"
    VMS_BASE_DIR:    str = "/vms"

    # ── Concurrencia ──────────────────────────────────────────────────────────
    MAX_CONCURRENT_WORKERS: int = 10

    # ── Healthcheck ───────────────────────────────────────────────────────────
    HEALTH_PORT: int = 8080

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
