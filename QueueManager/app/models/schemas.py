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


class VMSpec(BaseModel):
    """
    Especificación de una VM.
    Viene del Slice Manager (ya con tap_interfaces y MACs calculadas),
    se reenvía íntegra al Compute Provisioner.
    """
    vm_id:           str                = Field(..., description="ID único de la VM")
    worker_ip:       str                = Field(..., description="IP del worker destino")
    ssh_user:        str                = Field(..., description="Usuario SSH del worker")
    ssh_private_key: str                = Field(..., description="Llave privada PEM como string")
    vcpus:           int                = Field(..., ge=1)
    ram_mb:          int                = Field(..., ge=128)
    image_name:      str                = Field(..., description="Nombre de la imagen base")
    tap_interfaces:  List[TapInterface] = Field(default_factory=list,
                                                description="Interfaces TAP con MACs asignadas por el Slice Manager")
    priority:        Optional[int]      = Field(default=0, ge=0, le=39)


class VMResult(BaseModel):
    """Resultado de una VM individual, tal como lo reporta el Compute Provisioner."""
    vm_id:     str
    worker_ip: str
    pid:       Optional[int] = None
    vnc_port:  Optional[int] = None
    error:     Optional[str] = None


# ---------------------------------------------------------------------------
# Mensajes de entrada: desde el Slice Manager
# ---------------------------------------------------------------------------

class DeploySliceRequest(BaseModel):
    """
    Mensaje que publica el Slice Manager para desplegar un slice.
    NATS subject: slice.deploy
    """
    slice_id:   str          = Field(...)
    request_id: str          = Field(...)
    vms:        List[VMSpec] = Field(..., min_length=1)


class DestroySliceRequest(BaseModel):
    """
    Mensaje que publica el Slice Manager para destruir un slice.
    NATS subject: slice.destroy
    """
    slice_id:   str = Field(...)
    request_id: str = Field(...)


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
    COMPUTE = "compute"
    NETWORK = "network"   # reservado para uso futuro


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
