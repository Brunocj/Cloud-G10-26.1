"""
Schemas para el Network Orchestrator.
Define los contratos de entrada (desde el Queue Manager) y salida.
Basado en conexiones (enlaces lógicos) para soportar cualquier topología.
"""

from enum import Enum
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class ProvisioningStatus(str, Enum):
    SUCCESS = "success"
    ERROR   = "error"
    PARTIAL = "partial"

# ---------------------------------------------------------------------------
# Sub-modelos de entrada
# ---------------------------------------------------------------------------
class SecurityRule(BaseModel):
    """
    Regla de firewall de una VM. `protocol`/`allow_port` terminan interpolados
    directo en un comando `iptables` vía SSH (network_executor.py) para el
    Linux Cluster — sin este allowlist + rango, un valor arbitrario (el
    frontend restringe el <select>, pero nada impide pegarle directo a la API)
    permitía inyección de comandos con privilegios root en el worker.
    """
    # ge=0: ICMP no tiene puerto — tanto WebApp como placement_worker codifican
    # esa regla como allow_port=0. Sin el 0 acá, Pydantic rechaza el mensaje
    # ENTERO (DeployNetworkRequest se construye de una vez, no por-regla) y
    # tira abajo el deploy completo del slice apenas alguien agrega una regla
    # ICMP — no solo esa regla, todo el resto de VMs/enlaces del mensaje.
    allow_port: int = Field(..., ge=0, le=65535)
    protocol: Literal["tcp", "udp", "icmp"] = "tcp"

    @field_validator("protocol", mode="before")
    @classmethod
    def _normalize_protocol(cls, v):
        return v.lower() if isinstance(v, str) else v

class NetworkLink(BaseModel):
    """Representa un 'cable' (Capa 2) entre dos VMs en la infraestructura."""
    connection_id: str = Field(..., description="ID único del enlace en la BD")
    vlan_id:       int = Field(..., description="C-VID (tag interno/cliente) del enlace (ej. 100)")
    s_vlan_id:     int = Field(default=0, description="S-VID (tag externo/servicio) del slice; 0 = sin Q-in-Q")
    
    # Datos del Extremo A (VM 1)
    vm1_id:              str = Field(..., description="ID de la VM 1")
    vm1_worker_ip:       str = Field(..., description="IP del gateway SSH para el worker de la VM 1")
    vm1_worker_port:     Optional[int] = Field(default=22, description="Puerto SSH en el gateway para el worker de la VM 1")
    vm1_tap:             str = Field(..., description="Interfaz TAP creada por Compute (ej: tap-vm1)")
    vm1_ssh_user:        Optional[str] = Field(default=None, description="Usuario SSH del worker 1")
    vm1_ssh_private_key: Optional[str] = Field(default=None, description="Llave PEM del worker 1")
    vm1_security_rules:  List[SecurityRule] = Field(default_factory=list)
    
    # Datos del Extremo B (VM 2)
    vm2_id:              str = Field(..., description="ID de la VM 2")
    vm2_worker_ip:       str = Field(..., description="IP del gateway SSH para el worker de la VM 2")
    vm2_worker_port:     Optional[int] = Field(default=22, description="Puerto SSH en el gateway para el worker de la VM 2")
    vm2_tap:             str = Field(..., description="Interfaz TAP creada por Compute (ej: tap-vm2)")
    vm2_ssh_user:        Optional[str] = Field(default=None, description="Usuario SSH del worker 2")
    vm2_ssh_private_key: Optional[str] = Field(default=None, description="Llave PEM del worker 2")
    vm2_security_rules:  List[SecurityRule] = Field(default_factory=list)

class TapInterface(BaseModel):
    tap_name: str
    mac: str

class VMNetworkSpec(BaseModel):
    """Información de la VM necesaria para configurar su Gateway e Iptables."""
    vm_id:           str
    worker_ip:       str
    worker_port:     Optional[int] = 22
    ssh_user:        Optional[str] = None
    ssh_private_key: Optional[str] = None
    tap_interfaces:  List[TapInterface] = Field(default_factory=list)
    internet_access: int = 0
    external_ip:     Optional[str] = None
    # Reglas de entrada desde Internet (AWS-style): deny-by-default en el
    # camino IP externa/VPN → VM. Distintas de SecurityRule en NetworkLink
    # (esas gobiernan tráfico VM↔VM dentro del slice, por TAP de enlace).
    ingress_rules:   List[SecurityRule] = Field(default_factory=list)
    internal_ip:     str
    # Modo Edición (REQ-US-14): VM ya desplegada — no crear su puerto de
    # gestión; solo importan los puertos de los enlaces nuevos que la tocan.
    already_deployed: bool = False

class DeployNetworkRequest(BaseModel):
    """Payload recibido en network.deploy"""
    slice_id:             str
    request_id:           str
    availability_zone_id: int = Field(default=1, description="1=Linux Cluster, 2=OpenStack")
    mode:                 str = Field(default="deploy", description="deploy | extend (Modo Edición)")
    host_map:             dict = Field(default_factory=dict, description="vm_id → selected_host (del VMPlacement)")
    # Q-in-Q OpenStack: {host_de_nova: {ip,port,user,key}} para SSH a los computes
    compute_ssh_map:      Optional[dict] = Field(default=None)
    # VLAN de gestión del slice, ya reservada por SliceManager (única global).
    # Si viene None, se cae a la fórmula legado 1000+slice_id (compat).
    mgmt_vlan:            Optional[int] = Field(default=None)
    links:                List[NetworkLink]
    vms:                  List[VMNetworkSpec] = Field(default_factory=list)

class DestroyNetworkRequest(BaseModel):
    """Payload recibido en network.destroy"""
    slice_id:             str
    request_id:           str
    availability_zone_id: int = Field(default=1)
    mode:                 str = Field(default="full", description="full | shrink (eliminación parcial)")
    links:                Optional[List[NetworkLink]] = None
    vms:                  Optional[List[VMNetworkSpec]] = None
    # Para OpenStack destroy: UUIDs de puertos Neutron a borrar
    port_map:             Optional[dict] = Field(default=None, description="vm_id → {provider_port_id, floating_ip_id}")
    # Q-in-Q OpenStack: {host_de_nova: {ip,port,user,key}} para limpiar el dot1q-tunnel
    compute_ssh_map:      Optional[dict] = Field(default=None)
# ---------------------------------------------------------------------------
# Modelos de Salida (Respuesta al Queue Manager)
# ---------------------------------------------------------------------------
class LinkResult(BaseModel):
    connection_id: str
    error:         Optional[str] = None

class DeployNetworkResponse(BaseModel):
    slice_id:     str
    request_id:   str
    status:       ProvisioningStatus
    links_ok:     List[LinkResult] = Field(default_factory=list)
    links_failed: List[LinkResult] = Field(default_factory=list)
    # port_map: vm_id → {provider_port_id, floating_ip, floating_ip_id}
    # Vacío para Linux Cluster; relleno para OpenStack (lo consume ComputeProvisioner)
    port_map:     dict = Field(default_factory=dict)

class DestroyNetworkResponse(BaseModel):
    slice_id:   str
    request_id: str
    status:     ProvisioningStatus
    error:      Optional[str] = None