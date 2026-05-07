"""Configuración centralizada del Network Orchestrator."""

from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    SERVICE_NAME: str = "network-orchestrator"
    LOG_LEVEL:    str = "INFO"

    NATS_URL:       str = "nats://nats:4222"
    
    # Subjects de entrada (Queue Manager publica aquí mediante request/reply)
    QUEUE_DEPLOY:  str = "network.deploy"
    QUEUE_DESTROY: str = "network.destroy"

    SSH_TIMEOUT:     int = 30
    SSH_MAX_RETRIES: int = 3
    SSH_RETRY_DELAY: int = 5

    MAX_CONCURRENT_WORKERS: int = 10

    HEALTH_PORT: int = 8084 # Usamos el 8084 para no chocar con el 8080 y 8081

    WAN_INTERFACE: str = "ens3"  # Interfaz de salida a Internet en los workers (ajustar según tu entorno)

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

settings = Settings()