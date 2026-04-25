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

class VMSpec(BaseModel):
    """Especificación de una VM. Viene del Slice Manager, se reenvía al Compute Provisioner."""
    vm_id:           str           = Field(..., description="ID único de la VM")
    worker_ip:       str           = Field(..., description="IP del worker destino")
    ssh_user:        str           = Field(..., description="Usuario SSH del worker")
    ssh_private_key: str           = Field(..., description="Llave privada PEM como string")
    vcpus:           int           = Field(..., ge=1)
    ram_mb:          int           = Field(..., ge=128)
    image_name:      str           = Field(..., description="Nombre de la imagen base")
    priority:        Optional[int] = Field(default=0, ge=0, le=39)


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
    slice_id:   str          = Field(..., description="ID del slice")
    request_id: str          = Field(..., description="ID de la solicitud para correlación")
    vms:        List[VMSpec] = Field(..., min_length=1)


class DestroySliceRequest(BaseModel):
    """
    Mensaje que publica el Slice Manager para destruir un slice.
    NATS subject: slice.destroy
    """
    slice_id:   str = Field(..., description="ID del slice a destruir")
    request_id: str = Field(..., description="ID de la solicitud para correlación")


# ---------------------------------------------------------------------------
# Mensajes de salida: hacia el Slice Manager
# ---------------------------------------------------------------------------

class DeploySliceResponse(BaseModel):
    """
    Resultado del despliegue publicado de vuelta al Slice Manager.
    NATS subject: slice.result
    """
    slice_id:   str
    request_id: str
    status:     SliceStatus
    vms:        List[VMResult] = Field(default_factory=list)
    failed_vms: List[VMResult] = Field(default_factory=list)


class DestroySliceResponse(BaseModel):
    """
    Resultado de la destrucción publicado de vuelta al Slice Manager.
    NATS subject: slice.result
    """
    slice_id:      str
    request_id:    str
    status:        SliceStatus
    destroyed_vms: List[str]     = Field(default_factory=list)
    failed_vms:    List[str]     = Field(default_factory=list)
    error:         Optional[str] = None


# ---------------------------------------------------------------------------
# Estado interno de una operación (para rollback futuro)
# ---------------------------------------------------------------------------

class OperationStep(str, Enum):
    """
    Pasos completados de una operación de despliegue.
    Permite al encolador saber qué se completó y qué hay que revertir.
    TODO: agregar NETWORK cuando se integre el Network Orchestrator.
    """
    COMPUTE = "compute"
    NETWORK = "network"   # reservado para uso futuro


class OperationState(BaseModel):
    """
    Estado persistido en NATS KV de una operación en curso.
    Usado para coordinar pasos y habilitar rollback futuro.
    """
    slice_id:        str
    request_id:      str
    operation:       str                 # "deploy" o "destroy"
    completed_steps: List[OperationStep] = Field(default_factory=list)
    vms:             List[VMSpec]        = Field(default_factory=list)
    vm_results:      List[VMResult]      = Field(default_factory=list)
