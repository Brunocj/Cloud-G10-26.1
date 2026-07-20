"""
Schemas del Queue Manager.
Define los contratos de entrada (desde el Slice Manager)
y salida (hacia el Slice Manager y módulos internos).
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class SliceStatus(str, Enum):
    SUCCESS = "success"
    ERROR   = "error"
    PARTIAL = "partial"


# ---------------------------------------------------------------------------
# Sub-modelos compartidos
# ---------------------------------------------------------------------------

class TapInterface(BaseModel):
    """
    Interfaz TAP pre-calculada por el Slice Manager.
    El Queue Manager la reenvía al Compute Provisioner sin modificarla.
    """
    tap_name: str = Field(..., description="Nombre de la interfaz TAP, ej: tap-vm1-0")
    mac:      str = Field(..., description="Dirección MAC, ej: 52:54:00:A3:C7:00")
    pci_slot: Optional[int] = Field(
        None, description="Slot PCI explícito (ensN real) — reenviado tal cual al Compute Provisioner."
    )


class VMSpec(BaseModel):
    """
    Especificación de una VM.
    Viene del Slice Manager (ya con tap_interfaces y MACs calculadas),
    se reenvía íntegra al Compute Provisioner.
    """
    vm_id:           str                = Field(..., description="ID único de la VM")
    worker_ip:       str                = Field(..., description="IP del gateway SSH para este worker")
    worker_port:     Optional[int]      = Field(default=22, description="Puerto SSH en el gateway (ej: 5811-5814)")
    ssh_user:        Optional[str]      = Field(default=None, description="Usuario SSH del worker")
    ssh_private_key: Optional[str]      = Field(default=None, description="Llave privada PEM como string")
    vcpus:           int                = Field(..., ge=1)
    ram_mb:          float              = Field(..., ge=128) # 🔥 Cambiado a float
    disk_gb:         float              = Field(...)         # 🔥 Nuevo: Tamaño del disco
    image_path:      str                = Field(...)         # 🔥 Nuevo: Ruta exacta de la imagen (reemplaza image_name)
    vnc_port:        Optional[int]      = Field(default=None)         # 🔥 Nuevo: Puerto VNC
    vnc_display:     Optional[int]      = Field(default=None)         # 🔥 Nuevo: Display VNC
    tap_interfaces:  List[TapInterface] = Field(default_factory=list,
                                                description="Interfaces TAP con MACs asignadas por el Slice Manager")
    priority:        Optional[int]      = Field(default=0, ge=0, le=39)

    # 🔥 NUEVOS CAMPOS DEL R5: Para que Pydantic no los borre al recibirlos
    internet_access: int = 0
    external_ip:     Optional[str] = None
    internal_ip:     str = "0.0.0.0"

    # Credenciales de la VM para cloud-init
    vm_user:     Optional[str] = None   # Si None → se usa el nombre de la imagen
    vm_password: Optional[str] = None   # Si None → se usa "pucp2026"
    owner_ssh_public_key: Optional[str] = None   # Llave pública del dueño (REQ-US-02)
    image_default_username: Optional[str] = None   # Usuario real de cloud-init de la imagen (Image.default_username)

    # Modo Edición (REQ-US-14): VM ya desplegada que solo recibe NICs en caliente
    already_deployed:     bool = False
    provider_instance_id: Optional[str] = None   # UUID Nova (hot-attach OpenStack)

    # OpenStack: host físico asignado y puertos Neutron
    selected_host: Optional[str]  = None
    network_ports: Optional[dict] = None

    # Nombres legibles para recursos en el proveedor
    vm_label:   Optional[str] = None
    slice_name: Optional[str] = None


class VMResult(BaseModel):
    """Resultado de una VM individual, tal como lo reporta el Compute Provisioner."""
    vm_id:     str
    worker_ip: str
    pid:       Optional[int] = None
    vnc_port:  Optional[int] = None
    error:     Optional[str] = None
    # OpenStack fields
    provider_instance_id: Optional[str] = None
    vnc_url:              Optional[str] = None
    external_ip:          Optional[str] = None


# ---------------------------------------------------------------------------
# Mensajes de entrada: desde el Slice Manager
# ---------------------------------------------------------------------------
class SecurityRule(BaseModel):
    allow_port: int
    protocol: str  # "tcp" o "udp"

class NetworkLink(BaseModel):
    connection_id: str
    vlan_id: int                       # C-VID (tag interno/cliente) por enlace
    s_vlan_id: int = 0                  # S-VID (tag externo/servicio) por slice; 0 = sin Q-in-Q
    # Datos del extremo 1
    vm1_id: str
    vm1_worker_ip: str
    vm1_worker_port: Optional[int] = 22
    vm1_tap: str
    vm1_ssh_user: Optional[str] = None
    vm1_ssh_private_key: Optional[str] = None
    vm1_security_rules: List[SecurityRule] = Field(default_factory=list)
    # Datos del extremo 2
    vm2_id: str
    vm2_worker_ip: str
    vm2_worker_port: Optional[int] = 22
    vm2_tap: str
    vm2_ssh_user: Optional[str] = None
    vm2_ssh_private_key: Optional[str] = None
    vm2_security_rules: List[SecurityRule] = Field(default_factory=list)


class DeploySliceRequest(BaseModel):
    slice_id:             str
    request_id:           str
    availability_zone_id: int = Field(default=1, description="ID de la AZ destino (1=Linux Cluster, 2=OpenStack)")
    mode:                 str = Field(default="deploy", description="deploy | extend (Modo Edición REQ-US-14)")
    vms:                  List[VMSpec] = Field(..., min_length=1)
    links:                List[NetworkLink] = Field(default_factory=list)
    workers:              List[dict] = Field(default_factory=list)
    # Q-in-Q OpenStack: {host_de_nova: {ip,port,user,key}} — lo usa el NetworkOrchestrator
    compute_ssh_map:      Optional[dict] = Field(default=None)
    # VLAN de gestión del slice, reservada por SliceManager en la tabla `vlans`
    # (única global, coordinada con las VLANs de los enlaces). Si viene vacía,
    # el NetworkOrchestrator cae a la fórmula legado 1000+slice_id.
    mgmt_vlan:             Optional[int] = Field(default=None)

class DestroySliceRequest(BaseModel):
    """
    Mensaje que publica el Slice Manager para destruir un slice.
    NATS subject: slice.destroy
    """
    slice_id:             str = Field(...)
    request_id:           str = Field(...)
    availability_zone_id: int = Field(default=1, description="1=Linux Cluster, 2=OpenStack")
    mode:       str = Field(default="full", description="full | shrink (eliminación parcial, REQ-US-14)")
    vms:        List[VMSpec]      = Field(default_factory=list)
    links:      List[NetworkLink] = Field(default_factory=list)
    # Shrink: NICs a desconectar en caliente de VMs sobrevivientes
    unplugs:    List[dict]        = Field(default_factory=list)
    # Q-in-Q OpenStack: {host_de_nova: {ip,port,user,key}} — lo usa el NetworkOrchestrator
    compute_ssh_map: Optional[dict] = Field(default=None)


# ---------------------------------------------------------------------------
# Mensajes de salida: hacia el Slice Manager
# ---------------------------------------------------------------------------

class DeploySliceResponse(BaseModel):
    """Resultado del deploy publicado en slice.result"""
    slice_id:   str
    request_id: str
    status:     SliceStatus
    vms:        List[VMResult] = Field(default_factory=list)
    failed_vms: List[VMResult] = Field(default_factory=list)


class DestroySliceResponse(BaseModel):
    """Resultado del destroy publicado en slice.result"""
    slice_id:      str
    request_id:    str
    status:        SliceStatus
    destroyed_vms: List[str]        = Field(default_factory=list)
    failed_vms:    List[VMResult]   = Field(default_factory=list)
    error:         Optional[str]    = None


# ---------------------------------------------------------------------------
# Estado interno (KV store)
# ---------------------------------------------------------------------------

class OperationStep(str, Enum):
    PLACEMENT = "placement"   # Nuevo: fase de asignación física
    NETWORK   = "network"
    COMPUTE   = "compute"
    STATE     = "state"       # Nuevo: actualización de estado final


class OperationState(BaseModel):
    """
    Estado persistido de una operación en curso.
    Usado para coordinar pasos y habilitar rollback futuro.
    """
    slice_id:        str
    request_id:      str
    operation:       str
    completed_steps: List[OperationStep] = Field(default_factory=list)
    vms:             List[VMSpec]        = Field(default_factory=list)
    vm_results:      List[VMResult]      = Field(default_factory=list)
