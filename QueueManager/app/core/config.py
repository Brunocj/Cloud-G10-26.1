"""Configuración centralizada del Queue Manager."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):

    SERVICE_NAME: str = "queue-manager"
    LOG_LEVEL:    str = "INFO"

    # ── NATS ────────────────────────────────────────────────────────────────
    NATS_URL:    str = "nats://nats:4222"

    # Subjects de entrada (Slice Manager publica aquí)
    SUBJECT_DEPLOY:  str = "slice.deploy"
    SUBJECT_DESTROY: str = "slice.destroy"

    # Subject de salida (Queue Manager publica resultados aquí)
    SUBJECT_RESULT:  str = "slice.result"

    # Subjects hacia los módulos internos
    SUBJECT_COMPUTE_DEPLOY:  str = "compute.deploy"
    SUBJECT_COMPUTE_DESTROY: str = "compute.destroy"
    SUBJECT_COMPUTE_RESULT:  str = "compute.result"

    # JetStream: nombre del stream y KV bucket para estado
    JS_STREAM_NAME:  str = "SLICES"
    JS_KV_BUCKET:    str = "slice-state"

    # ── Timeouts ─────────────────────────────────────────────────────────────
    COMPUTE_TIMEOUT: int = 300   # segundos — tiempo máximo para que compute responda

    # ── Healthcheck ──────────────────────────────────────────────────────────
    HEALTH_PORT: int = 8080

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
