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
    ram_mb: float    # 🔥 Cambiado de int a float
    disk_gb: float   # 🔥 Cambiado de int a float

class WorkerState(BaseModel):
    """Capacidad disponible de un worker físico."""
    worker_id: int            # 🔥 Cambiado de str a int (¡Nuestro arreglo anterior!)
    available_vcpus: int
    available_ram_mb: float   # 🔥 Cambiado a float
    available_disk_gb: float  # 🔥 Cambiado a float

class PlacementRequest(BaseModel):
    """
    Cuerpo del POST /placement.
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
    worker_id: int   # 🔥 Cambiado de str a int para que devuelva el número correctamente

class FailureReason(str, Enum):
    INSUFFICIENT_RESOURCES = "INSUFFICIENT_RESOURCES"
    NO_WORKERS_AVAILABLE   = "NO_WORKERS_AVAILABLE"

class PlacementResponse(BaseModel):
    """Respuesta HTTP del endpoint POST /placement."""
    slice_id: str
    status: PlacementStatus
    placement_map: Optional[List[VMAssignment]] = None
    reason: Optional[FailureReason] = None
    detail: Optional[str] = None