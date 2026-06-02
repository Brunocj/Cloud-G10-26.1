from pydantic import BaseModel
from typing import List, Optional


class VMSpec(BaseModel):
    vm_id: str
    vcpus: float      # vCPUs solicitadas
    ram_gb: float     # RAM solicitada en GB
    disco_gb: float   # Disco solicitado en GB


class WorkerState(BaseModel):
    worker_id: int
    disponible_cpu: float    # C_efectivo_cpu[j] - Σ vcpus(VMs ACTIVE)
    disponible_ram: float    # C_efectivo_ram[j] - Σ ram_gb(VMs ACTIVE)
    disponible_disco: float  # C_efectivo_disco[j] - Σ disco_gb(VMs ACTIVE)


class PlacementRequest(BaseModel):
    slice_id: str
    availability_zone: str  # ID de la zona como string
    vms: List[VMSpec]
    workers: List[WorkerState]


class PlacementEntry(BaseModel):
    vm_id: str
    worker_id: int


class PlacementResponse(BaseModel):
    slice_id: str
    status: str                           # "SUCCESS" | "FAILED"
    placement_map: Optional[List[PlacementEntry]] = None
    reason: Optional[str] = None         # "NO_WORKERS_AVAILABLE" | "INSUFFICIENT_RESOURCES" | "TIMEOUT"
    detail: Optional[str] = None
