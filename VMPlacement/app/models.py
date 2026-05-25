from pydantic import BaseModel
from typing import List, Optional


class VMSpec(BaseModel):
    vm_id: str
    peso: float  # Pre-calculado por el Slice Manager al persistir la VM en BD


class WorkerState(BaseModel):
    worker_id: int
    disponible: float  # Capacidad disponible en unidades de peso (C_i - suma pesos VMs activas)


class PlacementRequest(BaseModel):
    slice_id: str
    availability_zone: str
    vms: List[VMSpec]
    workers: List[WorkerState]


class PlacementEntry(BaseModel):
    vm_id: str
    worker_id: int


class PlacementResponse(BaseModel):
    slice_id: str
    status: str                          # "SUCCESS" | "FAILED"
    placement_map: Optional[List[PlacementEntry]] = None
    reason: Optional[str] = None        # "NO_WORKERS_AVAILABLE" | "INSUFFICIENT_RESOURCES" | "TIMEOUT"
    detail: Optional[str] = None
