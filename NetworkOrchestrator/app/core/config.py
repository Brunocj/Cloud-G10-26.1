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
    EXTERNAL_INTERFACE: str = "br-int"  # Interfaz de datos para acceso exterior (DNAT)
    EXTERNAL_POOL_CIDR: str = "10.60.15.0/24"  # Pool de acceso exterior asignado (Linux Cluster)

    # Interfaz de datos (trunk L2) que conecta br-int entre workers. Se re-cuelga
    # a br-int en cada deploy (idempotente) para que la conectividad inter-worker
    # sobreviva reinicios/migraciones. Vacío = no gestionar el trunk.
    DATA_TRUNK_IFACE: str = "ens4"

    # ── OpenStack: red provider compartida para salida a Internet ──────────
    OS_EXTERNAL_NETWORK_NAME: str = "external"          # Red provider flat ya creada en OpenStack
    OS_EXTERNAL_SUBNET_NAME:  str = "external_subnet"
    OS_EXTERNAL_SUBNET_CIDR:  str = "10.60.16.0/24"      # Pool de acceso exterior asignado (OpenStack)
    OS_EXTERNAL_GATEWAY_IP:   str = "10.60.16.1"

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

settings = Settings()