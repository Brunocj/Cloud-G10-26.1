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

    if not request.workers:
        return PlacementResponse(
            slice_id=request.slice_id,
            status=PlacementStatus.FAILED,
            reason=FailureReason.NO_WORKERS_AVAILABLE,
            detail="La lista de workers está vacía.",
        )

    assignments: List[VMAssignment] = []
    num_workers = len(request.workers)

    for vm in request.vms:
        LAST_WORKER_INDEX = LAST_WORKER_INDEX % num_workers
        chosen_worker = request.workers[LAST_WORKER_INDEX]

        assignments.append(VMAssignment(vm_id=vm.vm_id, worker_id=chosen_worker.worker_id))
        logger.info(f"[{request.slice_id}] RR Puro Temporal: {vm.vm_id} → {chosen_worker.worker_id}")

        LAST_WORKER_INDEX = (LAST_WORKER_INDEX + 1) % num_workers

    return PlacementResponse(
        slice_id=request.slice_id,
        status=PlacementStatus.SUCCESS,
        placement_map=assignments,
    )