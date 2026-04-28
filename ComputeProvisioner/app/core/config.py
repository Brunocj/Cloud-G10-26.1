"""Configuración centralizada del Compute Provisioner."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):

    SERVICE_NAME: str = "compute-provisioner"
    LOG_LEVEL:    str = "INFO"

    # ── NATS ────────────────────────────────────────────────────────────────
    NATS_URL:        str = "nats://nats:4222"
    QUEUE_DEPLOY:    str = "compute.deploy"
    QUEUE_DESTROY:   str = "compute.destroy"
    NATS_KV_BUCKET:  str = "compute-state"

    # ── SSH ─────────────────────────────────────────────────────────────────
    SSH_TIMEOUT:    int = 30
    SSH_MAX_RETRIES: int = 3
    SSH_RETRY_DELAY: int = 5

    # ── Paths en workers ────────────────────────────────────────────────────
    IMAGES_BASE_DIR: str = "/images"
    VMS_BASE_DIR:    str = "/vms"

    # ── OVS ─────────────────────────────────────────────────────────────────
    OVS_BRIDGE: str = "br-int"   # bridge OVS único por worker

    # ── VNC ─────────────────────────────────────────────────────────────────
    VNC_PORT_MIN: int = 5901
    VNC_PORT_MAX: int = 5999

    # ── Concurrencia ────────────────────────────────────────────────────────
    MAX_CONCURRENT_WORKERS: int = 10

    # ── Healthcheck ─────────────────────────────────────────────────────────
    HEALTH_PORT: int = 8080

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
