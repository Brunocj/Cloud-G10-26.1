# vm-placement/app/placement_engine_temp.py

import logging
from typing import List
from app.models import (
    PlacementRequest, PlacementResponse,
    PlacementStatus, VMAssignment,
    FailureReason,
)

logger = logging.getLogger(__name__)

# Variable global persistente en memoria para el Round Robin Puro
LAST_WORKER_INDEX = 0

def run_placement_temp(request: PlacementRequest) -> PlacementResponse:
    global LAST_WORKER_INDEX

    logger.info("─"*60)
    logger.info("[VM-PLACEMENT] 🧠 Algoritmo: Round Robin — slice=%s  VMs=%d  workers=%d",
                request.slice_id, len(request.vms), len(request.workers))

    if not request.workers:
        logger.error("[VM-PLACEMENT] ❌ No hay workers disponibles para el placement")
        return PlacementResponse(
            slice_id=request.slice_id,
            status=PlacementStatus.FAILED,
            reason=FailureReason.NO_WORKERS_AVAILABLE,
            detail="La lista de workers está vacía.",
        )

    logger.info("[VM-PLACEMENT] Workers disponibles:")
    for w in request.workers:
        logger.info("[VM-PLACEMENT]   Worker-%-2d → vCPUs: %s  RAM: %s MB  Disco: %s GB",
                    w.worker_id,
                    getattr(w, 'available_vcpus', '?'),
                    getattr(w, 'available_ram_mb', '?'),
                    getattr(w, 'available_disk_gb', '?'))

    assignments: List[VMAssignment] = []
    num_workers = len(request.workers)

    for vm in request.vms:
        LAST_WORKER_INDEX = LAST_WORKER_INDEX % num_workers
        chosen_worker = request.workers[LAST_WORKER_INDEX]
        assignments.append(VMAssignment(vm_id=vm.vm_id, worker_id=chosen_worker.worker_id))
        logger.info("[VM-PLACEMENT]   ✅ %s → Worker-%d", vm.vm_id, chosen_worker.worker_id)
        LAST_WORKER_INDEX = (LAST_WORKER_INDEX + 1) % num_workers

    logger.info("[VM-PLACEMENT] 🏁 Placement completado: status=SUCCESS  total=%d asignaciones", len(assignments))
    logger.info("─"*60)
    return PlacementResponse(
        slice_id=request.slice_id,
        status=PlacementStatus.SUCCESS,
        placement_map=assignments,
    )