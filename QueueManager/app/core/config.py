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
    SUBJECT_NETWORK_DEPLOY:  str = "network.deploy"
    SUBJECT_NETWORK_DESTROY: str = "network.destroy"
    SUBJECT_NETWORK_RESULT:  str = "network.result"
    # Nuevos subjects de la Saga (Fase 2)
    SUBJECT_PLACEMENT:       str = "slice.placement.process"   # Orquestador → VMPlacement
    SUBJECT_STATE_UPDATE:    str = "slice.state.update"        # Orquestador → Slice Manager
    # JetStream: nombre del stream y KV bucket para estado
    JS_STREAM_NAME:  str = "SLICES"
    JS_KV_BUCKET:    str = "slice-state"

    # ── Timeouts ─────────────────────────────────────────────────────────────
    PLACEMENT_TIMEOUT: int = 30    # segundos — tiempo máximo para que VMPlacement responda
    COMPUTE_TIMEOUT:   int = 300   # segundos — tiempo máximo para que compute responda
    NETWORK_TIMEOUT:   int = 300   # aumentado: OVS en VMs anidadas tarda ~4s por comando
    # ── Healthcheck ──────────────────────────────────────────────────────────
    HEALTH_PORT: int = 8080

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
