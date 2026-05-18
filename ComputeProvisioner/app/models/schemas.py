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
    worker_ip:       str                 = Field(..., description="IP del worker destino")
    ssh_user:        str                 = Field(..., description="Usuario SSH del worker")
    ssh_private_key: str                 = Field(..., description="Llave privada PEM como string")
    vcpus:           int                 = Field(..., ge=1)
    ram_mb:          int                 = Field(..., ge=128)
    disk_gb: float
    image_path: str # 🔥 Ahora recibimos la ruta completa, no solo el nombre
    vnc_port: int

    # --- NUEVOS CAMPOS DEL R5 ---
    internet_access: int                 = Field(default=0, description="1 si tiene salida a internet")
    external_ip:     Optional[str]       = Field(default=None, description="IP pública/VPN asignada")
    internal_ip:     Optional[str]       = Field(default=None, description="IP interna asignada por el Queue Manager para NAT/Gateway")
    # ----------------------------

    # Credenciales cloud-init
    vm_user:     Optional[str] = Field(default=None, description="Usuario a crear en la VM (default: nombre de imagen)")
    vm_password: Optional[str] = Field(default=None, description="Contraseña de la VM (default: pucp2026)")

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


# ---------------------------------------------------------------------------
# Mensajes de entrada
# ---------------------------------------------------------------------------

class DeployRequest(BaseModel):
    """Mensaje recibido en compute.deploy"""
    slice_id:   str          = Field(...)
    request_id: str          = Field(...)
    vms:        List[VMSpec] = Field(..., min_length=1)


class DestroyRequest(BaseModel):
    """Mensaje recibido en compute.destroy"""
    slice_id:   str = Field(...)
    request_id: str = Field(...)


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
