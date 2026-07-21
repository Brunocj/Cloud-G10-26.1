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

    # MTU real del trunk inter-worker. Detectado en el cluster: los workers son
    # VMs anidadas y el uplink de ens4 hacia el switch virtual externo NO
    # soporta frames de 1500 en todas las combinaciones (confirmado con
    # ping -M do: falla justo a 1500, funciona parejo desde 1450 para abajo,
    # en cualquier enlace que toque el worker con menor margen real). ens4/
    # br-int seguían reportando 1500 igual — mentían sobre el techo real, sin
    # devolver el ICMP "frag needed" que permitiría a PMTUD ajustarse solo, y
    # SSH/TLS se colgaban justo en el primer paquete de key exchange más
    # grande que los iniciales. Mismo síntoma que ya resolvió OS_NETWORK_MTU
    # para OpenStack; acá faltaba el equivalente para Linux Cluster.
    # 0 = no forzar MTU (comportamiento previo).
    DATA_TRUNK_MTU: int = 1450

    # ── OpenStack: red provider compartida para salida a Internet ──────────
    OS_EXTERNAL_NETWORK_NAME: str = "external"          # Red provider flat ya creada en OpenStack
    OS_EXTERNAL_SUBNET_NAME:  str = "external_subnet"
    OS_EXTERNAL_SUBNET_CIDR:  str = "10.60.16.0/24"      # Pool de acceso exterior asignado (OpenStack)
    OS_EXTERNAL_GATEWAY_IP:   str = "10.60.16.1"

    # Banda que Neutron puede autoasignar dentro de la subnet externa. Es de
    # donde sale el puerto qg- de CADA router de slice. El resto del /24 queda
    # reservado para la tabla `ip_pool` del SliceManager, que reparte las
    # floating IPs que el usuario elige en el WebApp.
    #
    # Sin esta separación los dos asignadores compiten por el mismo rango —
    # Neutron desde abajo, `ip_pool` también desde abajo (query sin ORDER BY) —
    # y en cuanto se cruzan, Neutron devuelve 409 al pedir la FIP explícita y la
    # VM se queda sin acceso externo. Verificado en el cluster: Neutron SÍ acepta
    # una FIP con dirección explícita fuera del allocation_pool, que es lo que
    # hace viable el reparto.
    #
    # Solo aplica si este módulo crea la subnet externa (bootstrap). Si ya
    # existe, se respeta su configuración: ajustarla con
    #   openstack subnet set --no-allocation-pool \
    #       --allocation-pool start=...,end=... external_subnet
    OS_EXTERNAL_ALLOCATION_POOL: str = "10.60.16.2-10.60.16.99"   # vacío = pool completo

    # DNS que se entrega a las VMs del slice. En OpenStack la subnet de gestión
    # se crea con enable_dhcp=False (ver openstack_network_executor), así que NO
    # hay dnsmasq de Neutron que sirva de resolver: la única vía por la que la VM
    # recibe un nameserver es el network_data.json del config-drive, y Nova solo
    # lo incluye si la subnet trae `dns_nameservers`. Sin esto la VM sale a
    # Internet por IP pero no resuelve nombres (ping 8.8.8.8 OK, apt update no).
    # En Linux Cluster el equivalente lo cubre el dnsmasq del gateway del slice.
    OS_SUBNET_DNS_NAMESERVERS: str = "8.8.8.8,8.8.4.4"   # separados por coma; vacío = sin DNS

    # MTU de las redes del slice. Las redes de Neutron son de tipo `vlan` y
    # viajan etiquetadas 802.1Q sobre el trunk físico (ens4): el tag añade 4
    # bytes, así que una trama con 1500 bytes de payload ocupa 1504 en el cable
    # ("baby giant"). Si el trunk/switch no los admite, los paquetes grandes se
    # descartan SIN AVISO: el ping chico pasa y el handshake TCP también, pero
    # el intercambio de claves de SSH (~1KB+) muere y la sesión queda colgada.
    # 1496 = 1500 − 4 del tag VLAN. Neutron lo propaga a la VM por el
    # network_data.json del config-drive (la subnet va sin DHCP) y además hace
    # MSS clamping en el router del slice. 0 = no fijar MTU (comportamiento previo).
    OS_NETWORK_MTU: int = 1496

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

settings = Settings()