"""
Schemas para el Compute Provisioner.
Define los contratos de entrada (desde el Queue Manager vía NATS)
y salida (respuesta al Queue Manager).
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Sub-modelos
# ---------------------------------------------------------------------------

class TapInterface(BaseModel):
    """
    Interfaz TAP a crear en el worker antes de lanzar la VM.
    El bridge destino (br-int) es siempre fijo — no viaja en el mensaje.
    La MAC es pre-calculada por el Queue Manager para garantizar unicidad por slice.
    """
    tap_name: str = Field(..., description="Nombre de la interfaz TAP, ej: tap-vm1-0")
    mac:      str = Field(..., description="Dirección MAC, ej: 52:54:00:A3:C7:00")


class VMSpec(BaseModel):
    """Especificación de una VM individual a desplegar."""
    vm_id:           str                 = Field(..., description="ID único de la VM")
    worker_ip:       str                 = Field(..., description="IP del gateway SSH para este worker")
    worker_port:     Optional[int]       = Field(default=22, description="Puerto SSH en el gateway (ej: 5811-5814)")
    ssh_user:        Optional[str]       = Field(default=None, description="Usuario SSH del worker")
    ssh_private_key: Optional[str]       = Field(default=None, description="Llave privada PEM como string")
    vcpus:           int                 = Field(..., ge=1)
    ram_mb:          float               = Field(..., ge=128)
    disk_gb:         float               = Field(...)
    provider_flavor_id: Optional[str]    = Field(default=None, description="UUID Nova cacheado del flavor lógico usado (si ya fue materializado antes) — evita re-listar/crear en OpenStack")
    flavor_name:        Optional[str]    = Field(default=None, description="Nombre del flavor lógico elegido en la web (si la VM usa uno) — se usa como nombre del flavor Nova al materializarlo")
    image_path:      str                 = Field(...)
    vnc_port:        Optional[int]       = Field(default=None)
    vnc_display:     Optional[int]       = Field(default=None)

    # --- NUEVOS CAMPOS DEL R5 ---
    internet_access: int                 = Field(default=0, description="1 si tiene salida a internet")
    external_ip:     Optional[str]       = Field(default=None, description="IP pública/VPN asignada")
    internal_ip:     Optional[str]       = Field(default=None, description="IP interna asignada por el Queue Manager para NAT/Gateway")
    # ----------------------------

    # --- NUEVOS CAMPOS OPENSTACK ---
    selected_host:   Optional[str]       = Field(default=None, description="Host físico asignado (Nova hypervisor)")
    network_ports:   Optional[dict]      = Field(default_factory=dict, description="Puertos lógicos Neutron {provider_port_id, ...}")
    # Nombres legibles para recursos en el proveedor
    vm_label:        Optional[str]       = Field(default=None, description="Etiqueta legible de la VM (del canvas)")
    slice_name:      Optional[str]       = Field(default=None, description="Nombre del slice")
    # -------------------------------

    # Credenciales cloud-init
    vm_user:     Optional[str] = Field(default=None, description="Usuario a crear en la VM (default: nombre de imagen)")
    vm_password: Optional[str] = Field(default=None, description="Contraseña de la VM (default: pucp2026)")
    owner_ssh_public_key: Optional[str] = Field(default=None, description="Llave pública SSH del dueño del slice (REQ-US-02)")
    image_default_username: Optional[str] = Field(default=None, description="Usuario real de cloud-init de la imagen (Image.default_username) — fuente de verdad sobre el heurístico por nombre de archivo")

    # Modo Edición (REQ-US-14): VM ya desplegada — no lanzarla, solo conectar
    # en caliente las NICs de tap_interfaces (QMP en Linux / Nova en OpenStack).
    already_deployed:     bool = Field(default=False)
    provider_instance_id: Optional[str] = Field(default=None, description="UUID Nova de la instancia existente")

    tap_interfaces:  List[TapInterface]  = Field(default_factory=list,
                                                  description="Interfaces TAP a crear (orden = índice de NIC en QEMU)")
    priority:        Optional[int]       = Field(default=20, ge=0, le=39)


class VMResult(BaseModel):
    """Resultado de una VM individual reportado al Queue Manager."""
    vm_id:     str
    worker_ip: str
    pid:       Optional[int] = None
    vnc_port:  Optional[int] = None
    error:     Optional[str] = None
    # Campos para OpenStack
    provider_instance_id: Optional[str] = None
    vnc_url:              Optional[str] = None
    external_ip:          Optional[str] = None
    provider_flavor_id:   Optional[str] = None   # UUID Nova del flavor resuelto — se reporta para cachearlo


# ---------------------------------------------------------------------------
# Mensajes de entrada
# ---------------------------------------------------------------------------

class DeployRequest(BaseModel):
    """Mensaje recibido en compute.deploy"""
    slice_id:             str          = Field(...)
    request_id:           str          = Field(...)
    availability_zone_id: int          = Field(default=1, description="1=Linux Cluster, 2=OpenStack")
    vms:                  List[VMSpec] = Field(..., min_length=1)
    # Q-in-Q OpenStack: {s_vlan_id, compute_ssh_map:{host:{ip,port,user,key}}}
    qinq:                 Optional[dict] = Field(default=None)


class DestroyRequest(BaseModel):
    """Mensaje recibido en compute.destroy"""
    slice_id:             str = Field(...)
    request_id:           str = Field(...)
    availability_zone_id: int = Field(default=1, description="1=Linux Cluster, 2=OpenStack")
    mode:                 str = Field(default="full", description="full | shrink (REQ-US-14)")
    # Shrink: NICs a desconectar en caliente de VMs sobrevivientes
    unplugs:              List[dict] = Field(default_factory=list)
    # Q-in-Q OpenStack: {s_vlan_id, compute_ssh_map} para limpiar el patch al destruir
    qinq:                 Optional[dict] = Field(default=None)


# ---------------------------------------------------------------------------
# Mensajes de salida
# ---------------------------------------------------------------------------

class DeployStatus(str, Enum):
    SUCCESS = "success"
    ERROR   = "error"
    PARTIAL = "partial"


class DeployReply(BaseModel):
    """Respuesta al Queue Manager tras deploy."""
    slice_id:   str
    request_id: str
    status:     DeployStatus
    vms:        List[VMResult] = Field(default_factory=list)
    failed_vms: List[VMResult] = Field(default_factory=list)


class DestroyReply(BaseModel):
    """Respuesta al Queue Manager tras destroy."""
    slice_id:   str
    request_id: str
    status:     DeployStatus
    error:      Optional[str] = None


# ---------------------------------------------------------------------------
# Estado interno (KV store)
# ---------------------------------------------------------------------------

class OperationStep(str, Enum):
    COMPUTE = "compute"
    NETWORK = "network"


class OperationState(BaseModel):
    slice_id:        str
    request_id:      str
    operation:       str
    completed_steps: List[OperationStep] = Field(default_factory=list)
    vms:             List[VMSpec]        = Field(default_factory=list)
    vm_results:      List[VMResult]      = Field(default_factory=list)
