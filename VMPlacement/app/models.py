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
    # Nombre físico del host (BYOS). Linux: opcional (se deriva del worker_id).
    # OpenStack: nombre del host de Nova (worker1/2/3) para forzar el scheduler.
    host_name: Optional[str] = None


class PlacementRequest(BaseModel):
    slice_id:             str
    availability_zone_id: int = 1   # 1=Linux Cluster, 2=OpenStack (BYOS)
    vms:     List[VMSpec]
    workers: List[WorkerState] = []  # Vacío cuando az_id=OpenStack (se consulta dinámicamente)


class PlacementEntry(BaseModel):
    vm_id:         str
    worker_id:     int    # ID numérico del worker (Linux) o índice del hipervisor
    selected_host: str    # Nombre físico del host ganador (BYOS manifiesto de salida)
                          # Linux:     hostname del worker (ej: worker-2)
                          # OpenStack: nombre del hipervisor Nova (ej: compute-node-1)


class PlacementResponse(BaseModel):
    slice_id: str
    status: str                           # "SUCCESS" | "FAILED"
    placement_map: Optional[List[PlacementEntry]] = None
    reason: Optional[str] = None         # "NO_WORKERS_AVAILABLE" | "INSUFFICIENT_RESOURCES" | "TIMEOUT"
    detail: Optional[str] = None
