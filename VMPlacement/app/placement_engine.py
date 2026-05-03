"""
Motor de placement: algoritmo Round Robin GLOBAL.

Lógica modificada para entrega parcial:
  - Mantiene memoria (variable global) del último worker usado entre diferentes 
    peticiones HTTP.
  - Sigue verificando recursos (vcpus, ram, disco).
"""

import logging
from typing import List, Tuple, Optional

from app.models import (
    PlacementRequest, PlacementResponse,
    PlacementStatus, VMAssignment, VMRequest, WorkerState,
    FailureReason,
)

logger = logging.getLogger(__name__)

# 🔥 LA MAGIA ESTÁ AQUÍ: Variable global en memoria
# Al estar fuera de las funciones, su valor sobrevive entre distintas peticiones POST
GLOBAL_RR_INDEX = 0

def _find_worker(
    vm: VMRequest,
    workers: List[WorkerState],
    start: int,
) -> Tuple[Optional[int], Optional[WorkerState]]:
    """
    Recorre circularmente desde `start` buscando el primer worker
    con recursos suficientes para `vm`.
    """
    n = len(workers)
    for offset in range(n):
        idx = (start + offset) % n
        w = workers[idx]
        if (w.available_vcpus  >= vm.vcpus and
            w.available_ram_mb >= vm.ram_mb and
            w.available_disk_gb >= vm.disk_gb):
            return idx, w
    return None, None


def run_placement(request: PlacementRequest) -> PlacementResponse:
    global GLOBAL_RR_INDEX  # Declaramos que vamos a usar y modificar la variable global

    if not request.workers:
        return PlacementResponse(
            slice_id=request.slice_id,
            status=PlacementStatus.FAILED,
            reason=FailureReason.NO_WORKERS_AVAILABLE,
            detail="La lista de workers está vacía.",
        )

    workers = [w.model_copy() for w in request.workers]
    assignments: List[VMAssignment] = []
    
    # 🔥 En lugar de empezar en 0, empezamos donde se quedó la última petición
    rr_index = GLOBAL_RR_INDEX

    for vm in request.vms:
        idx, chosen = _find_worker(vm, workers, rr_index)

        if chosen is None:
            detail = (
                f"No hay worker con recursos suficientes para '{vm.vm_id}' "
                f"(vcpus={vm.vcpus}, ram_mb={vm.ram_mb}, disk_gb={vm.disk_gb})."
            )
            logger.warning(f"[{request.slice_id}] {detail}")
            return PlacementResponse(
                slice_id=request.slice_id,
                status=PlacementStatus.FAILED,
                reason=FailureReason.INSUFFICIENT_RESOURCES,
                detail=detail,
            )

        workers[idx].available_vcpus   -= vm.vcpus
        workers[idx].available_ram_mb  -= vm.ram_mb
        workers[idx].available_disk_gb -= vm.disk_gb

        assignments.append(VMAssignment(vm_id=vm.vm_id, worker_id=chosen.worker_id))
        logger.info(f"[{request.slice_id}] {vm.vm_id} → {chosen.worker_id}")
        
        # Avanzamos el índice para la SIGUIENTE VM
        rr_index = (idx + 1) % len(workers)

    # 🔥 GUARDAMOS EL ÍNDICE para el siguiente Slice/Request que llegue en el futuro
    GLOBAL_RR_INDEX = rr_index

    return PlacementResponse(
        slice_id=request.slice_id,
        status=PlacementStatus.SUCCESS,
        placement_map=assignments,
    )