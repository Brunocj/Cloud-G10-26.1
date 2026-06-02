"""
Placement Engine — CP-SAT (Google OR-Tools)

Modelo: knapsack determinístico multidimensional con capacidades efectivas
ajustadas estadísticamente (chance-constraint approximation).

Función objetivo:
    min( max_j( Σ_r α_r · Σ_i recurso_r(i) · x[i][j] / C_efectivo_r[j] ) )

    Coeficientes de importancia por dimensión:
        α_cpu = 3  |  β_ram = 5  |  γ_disco = 1

Restricciones:
    - x[i][j] ∈ {0, 1}
    - Σ_j x[i][j] = 1              (cada VM va a exactamente un worker)
    - Σ_i vcpus(i)   · x[i][j] ≤ disponible_cpu[j]    ∀ j
    - Σ_i ram_gb(i)  · x[i][j] ≤ disponible_ram[j]    ∀ j
    - Σ_i disco_gb(i)· x[i][j] ≤ disponible_disco[j]  ∀ j

Escala: los floats se multiplican por SCALE antes de pasar al solver (enteros).
El timeout se pasa directamente a CP-SAT vía parameters.max_time_in_seconds.
"""

from typing import List, Tuple

from ortools.sat.python import cp_model

from app.models import VMSpec, WorkerState, PlacementEntry

# Coeficientes de importancia relativa por dimensión
ALPHA_CPU   = 3
BETA_RAM    = 5
GAMMA_DISCO = 1

# Factor de escala para convertir floats a enteros
SCALE = 1000


def run_placement(
    vms: List[VMSpec],
    workers: List[WorkerState],
    timeout_seconds: float,
) -> Tuple[bool, List[PlacementEntry], str, str]:
    """
    Ejecuta CP-SAT y retorna (success, placement_map, reason, detail).

    Pasos:
      1. Validaciones previas.
      2. Construir modelo CP-SAT con variables, restricciones y objetivo.
      3. Resolver con time_limit = timeout_seconds.
      4. Interpretar resultado y retornar mapa o FAILED atómico.
    """

    # ── 1. Validaciones ───────────────────────────────────────────────────────
    if not workers:
        return False, [], "NO_WORKERS_AVAILABLE", "La lista de workers está vacía."

    if not vms:
        return True, [], None, None

    n = len(vms)
    m = len(workers)

    # Escalar recursos a enteros
    vcpus   = [round(v.vcpus   * SCALE) for v in vms]
    ram     = [round(v.ram_gb  * SCALE) for v in vms]
    disco   = [round(v.disco_gb* SCALE) for v in vms]

    cap_cpu   = [round(w.disponible_cpu   * SCALE) for w in workers]
    cap_ram   = [round(w.disponible_ram   * SCALE) for w in workers]
    cap_disco = [round(w.disponible_disco * SCALE) for w in workers]

    # Verificar factibilidad básica: ¿alguna VM supera la capacidad máxima en alguna dimensión?
    max_cpu   = max(cap_cpu)
    max_ram   = max(cap_ram)
    max_disco = max(cap_disco)

    for vm, vc, ra, di in zip(vms, vcpus, ram, disco):
        if vc > max_cpu:
            return (
                False, [],
                "INSUFFICIENT_RESOURCES",
                f"Ningún worker tiene CPU suficiente para VM '{vm.vm_id}' "
                f"(vcpus={vm.vcpus}, máx disponible={max(w.disponible_cpu for w in workers):.4f}).",
            )
        if ra > max_ram:
            return (
                False, [],
                "INSUFFICIENT_RESOURCES",
                f"Ningún worker tiene RAM suficiente para VM '{vm.vm_id}' "
                f"(ram_gb={vm.ram_gb}, máx disponible={max(w.disponible_ram for w in workers):.4f}).",
            )
        if di > max_disco:
            return (
                False, [],
                "INSUFFICIENT_RESOURCES",
                f"Ningún worker tiene disco suficiente para VM '{vm.vm_id}' "
                f"(disco_gb={vm.disco_gb}, máx disponible={max(w.disponible_disco for w in workers):.4f}).",
            )

    # ── 2. Modelo CP-SAT ──────────────────────────────────────────────────────
    model = cp_model.CpModel()

    # Variables de decisión: x[i][j] = 1 si VM i → worker j
    x = [[model.NewBoolVar(f"x_{i}_{j}") for j in range(m)] for i in range(n)]

    # Restricción: cada VM asignada a exactamente un worker
    for i in range(n):
        model.AddExactlyOne(x[i][j] for j in range(m))

    # Restricciones de capacidad por dimensión (tres independientes)
    for j in range(m):
        model.Add(sum(vcpus[i]   * x[i][j] for i in range(n)) <= cap_cpu[j])
        model.Add(sum(ram[i]     * x[i][j] for i in range(n)) <= cap_ram[j])
        model.Add(sum(disco[i]   * x[i][j] for i in range(n)) <= cap_disco[j])

    # Objetivo: minimizar makespan ponderado
    # makespan = max_j( Σ_r α_r · carga_r[j] / cap_r[j] )
    # CP-SAT requiere enteros: multiplicamos por SCALE² para preservar precisión
    # en la división — usamos sum * alpha / cap como entero redondeado.
    #
    # Para evitar división (no soportada directamente), reformulamos:
    # Minimizar Z tal que Z ≥ Σ_r α_r · (Σ_i recurso_r(i)·x[i][j]) * SCALE / cap_r[j]  ∀ j
    # Equivalentemente: Z · cap_r[j] ≥ α_r · Σ_i recurso_r(i)·x[i][j] · SCALE  ∀ j, r
    # Combinamos dimensiones en un único makespan ponderado por worker.

    # Carga ponderada de cada worker (en unidades de SCALE²):
    # load[j] = Σ_r α_r · Σ_i recurso_r(i) · x[i][j]  (ya escalado por SCALE)
    # Para el makespan necesitamos load[j] / cap_total_ponderado[j],
    # pero como las capacidades difieren por dimensión, usamos AddMaxEquality
    # sobre una variable de utilización por worker.

    # Definimos para cada worker j una variable entera que representa:
    # util[j] = max_r( α_r · carga_r[j] · SCALE / cap_r[j] )
    # Y luego makespan = max_j( util[j] )
    #
    # Para linearizar la división multiplicamos ambos lados:
    # util[j] · cap_r[j] ≥ α_r · carga_r[j] · SCALE

    # Rango máximo del objetivo (100% utilización en todas las dimensiones)
    MAX_UTIL = (BETA_RAM + ALPHA_CPU + GAMMA_DISCO) * SCALE * SCALE

    util = [model.NewIntVar(0, MAX_UTIL, f"util_{j}") for j in range(m)]

    for j in range(m):
        # Carga en cada dimensión para el worker j
        load_cpu   = sum(vcpus[i] * x[i][j] for i in range(n))
        load_ram   = sum(ram[i]   * x[i][j] for i in range(n))
        load_disco = sum(disco[i] * x[i][j] for i in range(n))

        # util[j] · cap_r[j] ≥ α_r · load_r · SCALE  (para cada dimensión r)
        # util[j] es el máximo de los tres — usamos AddMaxEquality con vars aux
        u_cpu   = model.NewIntVar(0, MAX_UTIL, f"u_cpu_{j}")
        u_ram   = model.NewIntVar(0, MAX_UTIL, f"u_ram_{j}")
        u_disco = model.NewIntVar(0, MAX_UTIL, f"u_disco_{j}")

        if cap_cpu[j] > 0:
            model.Add(u_cpu * cap_cpu[j] >= ALPHA_CPU * load_cpu * SCALE)
        if cap_ram[j] > 0:
            model.Add(u_ram * cap_ram[j] >= BETA_RAM  * load_ram * SCALE)
        if cap_disco[j] > 0:
            model.Add(u_disco * cap_disco[j] >= GAMMA_DISCO * load_disco * SCALE)

        model.AddMaxEquality(util[j], [u_cpu, u_ram, u_disco])

    makespan = model.NewIntVar(0, MAX_UTIL, "makespan")
    model.AddMaxEquality(makespan, util)
    model.Minimize(makespan)

    # ── 3. Resolver ───────────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = timeout_seconds
    solver.parameters.num_search_workers = 1  # determinístico

    status = solver.Solve(model)

    # ── 4. Interpretar resultado ──────────────────────────────────────────────
    if status == cp_model.INFEASIBLE:
        return (
            False, [],
            "INSUFFICIENT_RESOURCES",
            "No existe asignación factible: la zona no dispone de capacidad "
            "suficiente para todas las VMs del slice.",
        )

    if status == cp_model.UNKNOWN:
        return (
            False, [],
            "TIMEOUT",
            f"El solver no encontró solución factible en el tiempo máximo permitido "
            f"({timeout_seconds}s).",
        )

    # OPTIMAL o FEASIBLE — construir mapa
    placement_map: List[PlacementEntry] = []
    for i in range(n):
        for j in range(m):
            if solver.Value(x[i][j]) == 1:
                placement_map.append(
                    PlacementEntry(vm_id=vms[i].vm_id, worker_id=workers[j].worker_id)
                )
                break

    return True, placement_map, None, None
