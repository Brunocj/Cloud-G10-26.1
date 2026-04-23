"""
Motor de placement: algoritmo Round Robin.

Lógica:
  1. Itera las VMs en el orden en que llegan.
  2. Para cada VM, busca circularmente desde rr_index el primer worker
     con vcpus, ram y disco suficientes.
  3. Al asignar, descuenta los recursos comprometidos del worker
     para que las siguientes VMs vean la capacidad real restante.
  4. Si ningún worker puede alojar una VM → fallo total (sin placement parcial).

Stateless entre llamadas: cada PlacementRequest trae el estado completo.
"""

import logging
from typing import List, Tuple, Optional

from app.models import (
    PlacementRequest, PlacementResponse,
    PlacementStatus, VMAssignment, VMRequest, WorkerState,
    FailureReason,
)

logger = logging.getLogger(__name__)


def _find_worker(
    vm: VMRequest,
    workers: List[WorkerState],
    start: int,
) -> Tuple[Optional[int], Optional[WorkerState]]:
    """
    Recorre circularmente desde `start` buscando el primer worker
    con recursos suficientes para `vm`.
    Retorna (índice, worker) o (None, None).
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
    if not request.workers:
        return PlacementResponse(
            slice_id=request.slice_id,
            status=PlacementStatus.FAILED,
            reason=FailureReason.NO_WORKERS_AVAILABLE,
            detail="La lista de workers está vacía.",
        )

    workers = [w.model_copy() for w in request.workers]
    assignments: List[VMAssignment] = []
    rr_index = 0

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
        rr_index = (idx + 1) % len(workers)

    return PlacementResponse(
        slice_id=request.slice_id,
        status=PlacementStatus.SUCCESS,
        placement_map=assignments,
    )
