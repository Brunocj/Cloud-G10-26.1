"""Configuración centralizada del Compute Provisioner."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):

    SERVICE_NAME: str = "compute-provisioner"
    LOG_LEVEL:    str = "INFO"

    # ── NATS ────────────────────────────────────────────────────────────────
    NATS_URL:        str = "nats://nats:4222"
    QUEUE_DEPLOY:    str = "compute.deploy"
    QUEUE_DESTROY:   str = "compute.destroy"
    QUEUE_CONSOLE_REFRESH: str = "compute.console.refresh"
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

    # MTU real del trunk inter-worker (Linux Cluster). Ver DATA_TRUNK_MTU en
    # NetworkOrchestrator/app/core/config.py para el detalle completo: el
    # uplink de ens4 entre workers no soporta 1500 en todas las combinaciones
    # (confirmado con ping -M do). Bajar el MTU del lado del bridge/trunk en
    # el worker NO alcanza: el bridging es transparente y no fuerza el límite
    # sobre tráfico que solo está atravesando (viene de la VM), así que la VM
    # sigue mandando frames de 1500 igual — y como el descarte real ocurre en
    # un tramo invisible para el worker (fuera de su stack IP), nunca se
    # genera el ICMP "frag needed" que permitiría a la VM autoajustarse por
    # PMTUD. Sin este valor puesto en la propia interfaz del guest (vía
    # network-config), el mismo cuelgue de SSH/TLS puede repetirse en
    # cualquier VM cuyo enlace cruce ese tramo. 0 = no forzar MTU.
    DATA_TRUNK_MTU: int = 1450

    # ── VNC ─────────────────────────────────────────────────────────────────
    VNC_PORT_MIN: int = 5901
    VNC_PORT_MAX: int = 5999

    # ── Concurrencia ────────────────────────────────────────────────────────
    MAX_CONCURRENT_WORKERS: int = 10

    # ── Healthcheck ─────────────────────────────────────────────────────────
    HEALTH_PORT: int = 8080

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
