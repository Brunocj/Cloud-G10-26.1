"""
Placement Engine — LLF-D (Least Loaded First con ordenamiento Decreciente)

Función objetivo:
    min( max( carga_i / C_i ) )   ∀ i ∈ workers de la zona

Criterio de selección por iteración:
    worker* = argmax_i( D_i )     → implementado con Max Heap para O(1) acceso

Complejidad: O(n log n + n log m)
    - O(n log n): ordenamiento inicial de VMs por peso decreciente
    - O(n log m): n operaciones heapreplace sobre heap de m workers
"""

import heapq
from typing import List, Dict, Tuple

from app.models import VMSpec, WorkerState, PlacementEntry


def run_placement(
    vms: List[VMSpec],
    workers: List[WorkerState],
) -> Tuple[bool, List[PlacementEntry], str, str]:
    """
    Ejecuta LLF-D y retorna (success, placement_map, reason, detail).

    Pasos:
      1. Validaciones previas.
      2. Ordenar VMs de mayor a menor peso  → O(n log n)
      3. Construir Max Heap de workers       → O(m)
      4. Por cada VM: extraer worker más disponible, verificar capacidad,
         asignar y reinsertar en heap        → O(n log m)
      5. Retornar mapa completo o FAILED atómico.
    """

    # ── 1. Validaciones ──────────────────────────────────────────────────────
    if not workers:
        return False, [], "NO_WORKERS_AVAILABLE", "La lista de workers está vacía."

    if not vms:
        return True, [], None, None

    # ── 2. Ordenar VMs de mayor a menor peso ─────────────────────────────────
    sorted_vms = sorted(vms, key=lambda v: v.peso, reverse=True)

    # ── 3. Construir Max Heap ─────────────────────────────────────────────────
    # heapq es min-heap; negamos disponible para simular max-heap.
    # Entrada: (-disponible, worker_id, disponible_mutable)
    # Usamos lista mutable [disponible] para poder actualizar sin reconstruir.
    heap: List[Tuple[float, int, List[float]]] = []
    for w in workers:
        heap.append((-w.disponible, w.worker_id, [w.disponible]))
    heapq.heapify(heap)  # O(m)

    # ── 4. Asignación iterativa ───────────────────────────────────────────────
    placement_map: List[PlacementEntry] = []

    for vm in sorted_vms:
        # O(1): el worker con mayor disponible siempre está en heap[0]
        neg_disp, worker_id, disp_ref = heap[0]
        disponible = disp_ref[0]

        # Fórmula 8: condición de fallo
        if disponible < vm.peso:
            return (
                False,
                [],
                "INSUFFICIENT_RESOURCES",
                f"No hay worker con capacidad suficiente para VM '{vm.vm_id}' "
                f"(peso={vm.peso:.4f}, máx disponible={disponible:.4f}).",
            )

        # Asignar
        placement_map.append(PlacementEntry(vm_id=vm.vm_id, worker_id=worker_id))

        # Fórmula 5: descontar peso y reordenar heap → O(log m)
        nuevo_disp = disponible - vm.peso
        disp_ref[0] = nuevo_disp
        heapq.heapreplace(heap, (-nuevo_disp, worker_id, disp_ref))

    return True, placement_map, None, None
