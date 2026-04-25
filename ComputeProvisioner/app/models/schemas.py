"""
Schemas para el Compute Provisioner.
Define los contratos de entrada (desde el Slice Manager vía encolador)
y salida (hacia el módulo de colas).
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


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

class VMSpec(BaseModel):
    """Especificación de una VM individual a desplegar."""
    vm_id:           str = Field(..., description="ID único de la VM")
    worker_ip:       str = Field(..., description="IP del worker destino")
    ssh_user:        str = Field(..., description="Usuario SSH del worker")
    ssh_private_key: str = Field(..., description="Llave privada PEM como string")
    vcpus:           int = Field(..., ge=1, description="Número de vCPUs")
    ram_mb:          int = Field(..., ge=128, description="RAM en MB")
    image_name:      str = Field(..., description="Nombre de la imagen base en el catálogo")
    priority:        Optional[int] = Field(
                         default=0, ge=0, le=39,
                         description="Nice value del proceso QEMU (0=normal, 19=baja prioridad)"
                     )


# ---------------------------------------------------------------------------
# Mensaje de entrada: DEPLOY_SLICE
# ---------------------------------------------------------------------------

class DeploySliceRequest(BaseModel):
    """
    Mensaje recibido desde el módulo de colas con la orden de desplegar un slice.
    El VM Placement ya calculó la asignación VM→worker antes de publicar este evento.
    El puerto VNC NO viene en el mensaje — lo asigna este módulo internamente.
    """
    slice_id:   str          = Field(..., description="ID del slice a desplegar")
    request_id: str          = Field(..., description="ID de la solicitud para correlación")
    vms:        List[VMSpec] = Field(..., min_length=1)


# ---------------------------------------------------------------------------
# Mensaje de entrada: DESTROY_SLICE
# ---------------------------------------------------------------------------

class DestroySliceRequest(BaseModel):
    """
    Mensaje recibido desde el módulo de colas con la orden de destruir un slice.
    """
    slice_id:   str = Field(..., description="ID del slice a destruir")
    request_id: str = Field(..., description="ID de la solicitud para correlación")


# ---------------------------------------------------------------------------
# Sub-modelos de salida
# ---------------------------------------------------------------------------

class VMResult(BaseModel):
    """Resultado del despliegue de una VM individual."""
    vm_id:     str
    worker_ip: str
    pid:       Optional[int] = None
    vnc_port:  Optional[int] = None   # puerto asignado por este módulo
    error:     Optional[str] = None


# ---------------------------------------------------------------------------
# Mensajes de salida hacia el módulo de colas
# ---------------------------------------------------------------------------

class DeploySliceResponse(BaseModel):
    """
    Resultado del despliegue publicado al módulo de colas.
    El encolador se encargará de notificar al Slice Manager.
    """
    slice_id:   str
    request_id: str
    status:     ProvisioningStatus
    vms:        List[VMResult] = Field(default_factory=list)
    failed_vms: List[VMResult] = Field(default_factory=list)


class DestroySliceResponse(BaseModel):
    """
    Resultado de la destrucción publicado al módulo de colas.
    """
    slice_id:      str
    request_id:    str
    status:        ProvisioningStatus
    destroyed_vms: List[str]     = Field(default_factory=list)
    failed_vms:    List[str]     = Field(default_factory=list)
    error:         Optional[str] = None
