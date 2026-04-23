"""
Modelos de entrada y salida del VM Placement.
Solo contiene los campos estrictamente necesarios para ejecutar el algoritmo.
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# INPUT
# ---------------------------------------------------------------------------

class VMRequest(BaseModel):
    """Requerimientos de recursos de una VM individual."""
    vm_id: str
    vcpus: int
    ram_mb: int
    disk_gb: int


class WorkerState(BaseModel):
    """Capacidad disponible de un worker físico."""
    worker_id: str
    available_vcpus: int
    available_ram_mb: int
    available_disk_gb: int


class PlacementRequest(BaseModel):
    """
    Cuerpo del POST /placement.
    Los workers ya vienen filtrados por availability_zone desde el Slice Manager.
    """
    slice_id: str
    availability_zone: str
    vms: List[VMRequest]
    workers: List[WorkerState]


# ---------------------------------------------------------------------------
# OUTPUT
# ---------------------------------------------------------------------------

class PlacementStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILED  = "FAILED"


class VMAssignment(BaseModel):
    """Asignación de una VM a un worker."""
    vm_id: str
    worker_id: str


class FailureReason(str, Enum):
    INSUFFICIENT_RESOURCES = "INSUFFICIENT_RESOURCES"
    NO_WORKERS_AVAILABLE   = "NO_WORKERS_AVAILABLE"


class PlacementResponse(BaseModel):
    """
    Respuesta HTTP del endpoint POST /placement.
    - SUCCESS: placement_map contiene la asignación VM→Worker.
    - FAILED:  reason y detail describen el motivo.
    """
    slice_id: str
    status: PlacementStatus
    placement_map: Optional[List[VMAssignment]] = None
    reason: Optional[FailureReason] = None
    detail: Optional[str] = None
