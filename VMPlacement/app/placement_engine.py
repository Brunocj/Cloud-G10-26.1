"""
Placement Engine — PUCP Cloud Orchestrator
==========================================

Patrón Strategy: el motor de asignación es completamente agnóstico a la nube.
La misma función objetivo CP-SAT (multidimensional weighted min-makespan)
se aplica independientemente de si los datos de capacidad vienen de:

  · Linux Cluster  → WorkerState enviados por el SliceManager (Prometheus-based)
  · OpenStack      → Nova Hypervisors API consultada por este módulo (BYOS)

Función objetivo (sin cambios):
    min( max_j( Σ_r α_r · Σ_i recurso_r(i) · x[i][j] / C_efectivo_r[j] ) )
    α_cpu = 3  |  β_ram = 5  |  γ_disco = 1

Restricciones:
    - x[i][j] ∈ {0, 1}
    - Σ_j x[i][j] = 1              (cada VM va a exactamente un worker)
    - Σ_i vcpus(i)   · x[i][j] ≤ disponible_cpu[j]    ∀ j
    - Σ_i ram_gb(i)  · x[i][j] ≤ disponible_ram[j]    ∀ j
    - Σ_i disco_gb(i)· x[i][j] ≤ disponible_disco[j]  ∀ j

Manifiesto de salida (BYOS): cada PlacementEntry lleva `selected_host` con el
nombre exacto del hipervisor/nodo físico ganador, para que Nova pueda ser
forzado a instanciar ahí (bypass del Nova-scheduler).
"""

import logging
import os
from typing import List, Optional, Tuple

from ortools.sat.python import cp_model

from app.models import VMSpec, WorkerState, PlacementEntry

logger = logging.getLogger("vm-placement.engine")

# ── Coeficientes de importancia relativa por dimensión ───────────────────────
ALPHA_CPU   = 3
BETA_RAM    = 5
GAMMA_DISCO = 1

# Factor de escala para convertir floats a enteros (CP-SAT requiere enteros)
SCALE = 1000

# ID de AZ por convención (configurable via env)
AZ_ID_LINUX      = int(os.getenv("LINUX_AZ_ID",     "1"))
AZ_ID_OPENSTACK  = int(os.getenv("OPENSTACK_AZ_ID", "2"))


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY: Recolección de datos de capacidad
# ══════════════════════════════════════════════════════════════════════════════

def _collect_provided_workers(workers_in: List[WorkerState]) -> Tuple[List[WorkerState], List[str]]:
    """
    Strategy AGNÓSTICA (Linux Cluster y OpenStack):
    Usa los WorkerState enviados por el SliceManager, ya calculados desde la BD
    (capacidad efectiva con overcommit − uso de VMs ACTIVE). VMPlacement queda
    ciego a la zona: solo resuelve el bin-packing con las capacidades recibidas.

    El nombre físico del host (BYOS) sale de `host_name` si el caller lo envía
    (OpenStack: host de Nova); si no, se deriva del worker_id (Linux legado).
    Retorna (workers, hostnames).
    """
    host_names = [w.host_name or f"worker-{w.worker_id}" for w in workers_in]
    return workers_in, host_names


def _collect_openstack_workers() -> Tuple[List[WorkerState], List[str]]:
    """
    Strategy OPENSTACK (BYOS):
    Consulta la API Nova Hypervisors vía openstacksdk para obtener métricas
    de capacidad de los nodos físicos. Aplica los mismos factores de overcommit
    que Linux para el cálculo de disponible_*.

    Variables de entorno requeridas:
      OS_AUTH_URL, OS_USERNAME, OS_PASSWORD, OS_PROJECT_NAME,
      OS_USER_DOMAIN_NAME, OS_PROJECT_DOMAIN_NAME

    Retorna (workers, host_names) para el solver CP-SAT.
    """
    import openstack  # openstacksdk — nunca SSH
    try:
        import socks  # PySocks
    except ImportError:
        logger.error("[BYOS][OpenStack] CRITICAL: PySocks no está instalado. ¡La imagen Docker no se ha reconstruido correctamente!")
        raise RuntimeError("Falta la librería PySocks. Por favor reconstruye la imagen Docker.")

    OC_CPU   = float(os.getenv("OS_OC_CPU",   "2.0"))   # overcommit CPU OpenStack
    OC_RAM   = float(os.getenv("OS_OC_RAM",   "1.54"))  # overcommit RAM OpenStack
    OC_DISCO = float(os.getenv("OS_OC_DISCO", "1.0"))   # overcommit Disco OpenStack

    # Forzar soporte estricto de proxy para requests/openstacksdk
    if os.getenv("HTTP_PROXY"):
        os.environ["http_proxy"] = os.getenv("HTTP_PROXY")
    if os.getenv("HTTPS_PROXY"):
        os.environ["https_proxy"] = os.getenv("HTTPS_PROXY")
    if os.getenv("NO_PROXY"):
        os.environ["no_proxy"] = os.getenv("NO_PROXY")

    conn = openstack.connect(
        auth_url=os.getenv("OS_AUTH_URL"),
        username=os.getenv("OS_USERNAME", "admin"),
        password=os.getenv("OS_PASSWORD", ""),
        project_name=os.getenv("OS_PROJECT_NAME", "admin"),
        user_domain_name=os.getenv("OS_USER_DOMAIN_NAME", "Default"),
        project_domain_name=os.getenv("OS_PROJECT_DOMAIN_NAME", "Default"),
    )

    workers: List[WorkerState] = []
    host_names: List[str] = []
    worker_id_counter = 1

    # Construir mapa hypervisor_hostname → service host (nombre que usa el scheduler)
    # `openstack compute service list` devuelve el campo `host` que es el que acepta
    # scheduler_hints.force_hosts. Sin este mapa usaríamos hypervisor_hostname que
    # puede diferir (ej: FQDN vs short name) y causaría "No valid host was found".
    hyp_to_service_host: dict = {}
    try:
        for svc in conn.compute.services(binary="nova-compute"):
            svc_host = getattr(svc, "host", None)
            if svc_host:
                hyp_to_service_host[svc_host] = svc_host   # identidad directa
    except Exception as e:
        logger.warning("[BYOS][OpenStack] No se pudo listar nova-compute services: %s. Se usará hypervisor_hostname.", e)

    logger.info("[BYOS][OpenStack] Consultando Nova Hypervisors API …")

    for hyp in conn.compute.hypervisors():
        # En Nova 22.x (Yoga) openstacksdk expone el campo como hypervisor_hostname
        hyp_name = getattr(hyp, "hypervisor_hostname", None) or getattr(hyp, "name", None) or str(getattr(hyp, "id", "unknown"))
        # Preferir el nombre del servicio compute (usado por scheduler_hints.force_hosts)
        hyp_name = hyp_to_service_host.get(hyp_name, hyp_name)
        
        hyp_state = getattr(hyp, "state", "up")
        hyp_status = getattr(hyp, "status", "enabled")

        # Solo incluir hipervisores KVM activos
        if hyp_state != "up" or hyp_status != "enabled":
            logger.debug("[BYOS] Hipervisor '%s' excluido (state=%s status=%s)",
                         hyp_name, hyp_state, hyp_status)
            continue

        # Capacidades nominales (Nova reporta vCPUs totales y RAM en MB)
        vcpus_total   = float(getattr(hyp, "vcpus", getattr(hyp, "vcpus_total", 8)) or 8)
        vcpus_used    = float(getattr(hyp, "vcpus_used", 0) or 0)
        
        # RAM: memory_size (openstacksdk property), memory_mb (nova raw field)
        ram_total_mb  = float(getattr(hyp, "memory_size", getattr(hyp, "memory_mb", 16384)) or 16384)
        ram_used_mb   = float(getattr(hyp, "memory_used", getattr(hyp, "memory_mb_used", 0)) or 0)
        
        # Disco: local_disk_size (openstacksdk property), local_gb (nova raw field)
        disco_total   = float(getattr(hyp, "local_disk_size", getattr(hyp, "local_gb", 100)) or 100)
        disco_used    = float(getattr(hyp, "local_disk_used", getattr(hyp, "local_gb_used", 0)) or 0)
        
        # Log properties in case it's missing (for debugging)
        if getattr(hyp, "memory_size", getattr(hyp, "memory_mb", None)) is None:
            logger.warning("[BYOS] Atributo de RAM no encontrado en Hypervisor '%s'. Propiedades disponibles: %s", 
                           hyp_name, hyp.to_dict() if hasattr(hyp, "to_dict") else dir(hyp))

        # Capacidades efectivas con overcommit (misma fórmula que Linux Cluster)
        c_ef_cpu   = vcpus_total   * OC_CPU
        c_ef_ram   = (ram_total_mb / 1024.0) * OC_RAM     # MB → GB
        c_ef_disco = disco_total   * OC_DISCO

        # Disponible = efectivo − usado
        disp_cpu   = max(0.0, c_ef_cpu   - vcpus_used)
        disp_ram   = max(0.0, c_ef_ram   - (ram_used_mb / 1024.0))
        disp_disco = max(0.0, c_ef_disco - disco_used)

        logger.info(
            "[BYOS][OpenStack] Hipervisor '%s' — "
            "cpu: total=%.1f used=%.1f oc=%.2f disp=%.1f | "
            "ram(GB): total=%.1f used=%.1f oc=%.2f disp=%.1f | "
            "disco(GB): total=%.1f used=%.1f oc=%.2f disp=%.1f",
            hyp_name,
            vcpus_total,   vcpus_used,   OC_CPU,   disp_cpu,
            ram_total_mb / 1024.0, ram_used_mb / 1024.0, OC_RAM, disp_ram,
            disco_total,  disco_used,   OC_DISCO, disp_disco,
        )

        workers.append(WorkerState(
            worker_id=worker_id_counter,
            disponible_cpu=disp_cpu,
            disponible_ram=disp_ram,
            disponible_disco=disp_disco,
        ))
        host_names.append(hyp_name)   # nombre exacto para el manifiesto BYOS
        worker_id_counter += 1

    logger.info("[BYOS][OpenStack] %d hipervisores activos encontrados.", len(workers))
    return workers, host_names


# ══════════════════════════════════════════════════════════════════════════════
# SOLVER CP-SAT — común para ambas estrategias
# ══════════════════════════════════════════════════════════════════════════════

def _solve_cpsat(
    vms: List[VMSpec],
    workers: List[WorkerState],
    host_names: List[str],
    timeout_seconds: float,
) -> Tuple[bool, List[PlacementEntry], Optional[str], Optional[str]]:
    """
    Ejecuta CP-SAT y retorna (success, placement_map, reason, detail).

    El placement_map incluye `selected_host` = nombre físico del nodo ganador.
    """
    if not workers:
        return False, [], "NO_WORKERS_AVAILABLE", "La lista de workers/hipervisores está vacía."

    if not vms:
        return True, [], None, None

    n = len(vms)
    m = len(workers)

    # Escalar recursos a enteros
    vcpus   = [round(v.vcpus    * SCALE) for v in vms]
    ram     = [round(v.ram_gb   * SCALE) for v in vms]
    disco   = [round(v.disco_gb * SCALE) for v in vms]

    cap_cpu   = [round(w.disponible_cpu   * SCALE) for w in workers]
    cap_ram   = [round(w.disponible_ram   * SCALE) for w in workers]
    cap_disco = [round(w.disponible_disco * SCALE) for w in workers]

    # Factibilidad básica: ¿alguna VM supera la capacidad máxima?
    max_cpu   = max(cap_cpu)
    max_ram   = max(cap_ram)
    max_disco = max(cap_disco)

    for vm, vc, ra, di in zip(vms, vcpus, ram, disco):
        if vc > max_cpu:
            return (
                False, [], "INSUFFICIENT_RESOURCES",
                f"Ningún host tiene CPU suficiente para VM '{vm.vm_id}' "
                f"(vcpus={vm.vcpus}, máx disponible={max(w.disponible_cpu for w in workers):.4f}).",
            )
        if ra > max_ram:
            return (
                False, [], "INSUFFICIENT_RESOURCES",
                f"Ningún host tiene RAM suficiente para VM '{vm.vm_id}' "
                f"(ram_gb={vm.ram_gb}, máx disponible={max(w.disponible_ram for w in workers):.4f}).",
            )
        if di > max_disco:
            return (
                False, [], "INSUFFICIENT_RESOURCES",
                f"Ningún host tiene disco suficiente para VM '{vm.vm_id}' "
                f"(disco_gb={vm.disco_gb}, máx disponible={max(w.disponible_disco for w in workers):.4f}).",
            )

    # ── Modelo CP-SAT ──────────────────────────────────────────────────────────
    model = cp_model.CpModel()

    # Variables de decisión: x[i][j] = 1 si VM i → host j
    x = [[model.NewBoolVar(f"x_{i}_{j}") for j in range(m)] for i in range(n)]

    # Restricción: cada VM asignada a exactamente un host
    for i in range(n):
        model.AddExactlyOne(x[i][j] for j in range(m))

    # Restricciones de capacidad por dimensión
    for j in range(m):
        model.Add(sum(vcpus[i] * x[i][j] for i in range(n)) <= cap_cpu[j])
        model.Add(sum(ram[i]   * x[i][j] for i in range(n)) <= cap_ram[j])
        model.Add(sum(disco[i] * x[i][j] for i in range(n)) <= cap_disco[j])

    # Objetivo: minimizar makespan ponderado (balance de carga multidimensional)
    # util[j] · cap_r[j] ≥ α_r · carga_r[j] · SCALE  ∀ j, r
    MAX_UTIL = (BETA_RAM + ALPHA_CPU + GAMMA_DISCO) * SCALE * SCALE

    util = [model.NewIntVar(0, MAX_UTIL, f"util_{j}") for j in range(m)]

    for j in range(m):
        load_cpu   = sum(vcpus[i] * x[i][j] for i in range(n))
        load_ram   = sum(ram[i]   * x[i][j] for i in range(n))
        load_disco = sum(disco[i] * x[i][j] for i in range(n))

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

    # ── Resolver ────────────────────────────────────────────────────────────────
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = timeout_seconds
    solver.parameters.num_search_workers  = 1  # determinístico

    status = solver.Solve(model)

    if status == cp_model.INFEASIBLE:
        return (
            False, [], "INSUFFICIENT_RESOURCES",
            "No existe asignación factible: la zona no dispone de capacidad "
            "suficiente para todas las VMs del slice.",
        )

    if status == cp_model.UNKNOWN:
        return (
            False, [], "TIMEOUT",
            f"El solver no encontró solución factible en {timeout_seconds}s.",
        )

    # OPTIMAL o FEASIBLE — construir manifiesto BYOS
    placement_map: List[PlacementEntry] = []
    for i in range(n):
        for j in range(m):
            if solver.Value(x[i][j]) == 1:
                placement_map.append(
                    PlacementEntry(
                        vm_id=vms[i].vm_id,
                        worker_id=workers[j].worker_id,
                        # ← Campo crítico BYOS: nombre exacto del nodo físico ganador
                        selected_host=host_names[j],
                    )
                )
                break

    return True, placement_map, None, None


# ══════════════════════════════════════════════════════════════════════════════
# FUNCIÓN PÚBLICA — punto de entrada del módulo
# ══════════════════════════════════════════════════════════════════════════════

def run_placement(
    vms: List[VMSpec],
    workers: List[WorkerState],
    timeout_seconds: float,
    availability_zone_id: int = AZ_ID_LINUX,
) -> Tuple[bool, List[PlacementEntry], Optional[str], Optional[str]]:
    """
    Ejecuta el motor de placement con la estrategia adecuada según az_id.

    Strategy Pattern:
      az_id == AZ_ID_LINUX      → usa WorkerState enviados (consulta Prometheus externa)
      az_id == AZ_ID_OPENSTACK  → consulta Nova Hypervisors API vía openstacksdk

    En ambos casos pasa los datos crudos (normalizados) por el mismo
    algoritmo CP-SAT (R4 de la rúbrica — sin cambios).

    Retorna:
      (success, placement_map, reason, detail)
      donde placement_map[i].selected_host = nombre físico del host ganador.
    """
    # ── Strategy AGNÓSTICA ─────────────────────────────────────────────────────
    # Si el caller envió inventario (WorkerState desde la BD), se usa para AMBAS
    # zonas por el mismo camino. VMPlacement no consulta a ningún backend de zona.
    if workers:
        logger.info("[PLACEMENT] az_id=%d → %d workers recibidos de la BD (agnóstico)",
                    availability_zone_id, len(workers))
        norm_workers, host_names = _collect_provided_workers(workers)
        return _solve_cpsat(vms, norm_workers, host_names, timeout_seconds)

    # ── Fallback legado ────────────────────────────────────────────────────────
    # OpenStack sin inventario recibido → consultar Nova (compatibilidad).
    if availability_zone_id == AZ_ID_OPENSTACK:
        logger.info("[BYOS] az_id=%d sin workers → fallback a Nova Hypervisors API", availability_zone_id)
        try:
            os_workers, host_names = _collect_openstack_workers()
        except Exception as exc:
            logger.error("[BYOS][OpenStack] Error conectando a Nova: %s", exc)
            return (
                False, [], "OPENSTACK_UNREACHABLE",
                f"No se pudo consultar la API de Nova Hypervisors: {exc}",
            )
        if not os_workers:
            return (
                False, [], "NO_WORKERS_AVAILABLE",
                "OpenStack no reportó hipervisores activos en la zona.",
            )
        logger.info("[BYOS][OpenStack] %d hipervisores (fallback Nova) para el solver.", len(os_workers))
        return _solve_cpsat(vms, os_workers, host_names, timeout_seconds)

    # Linux (o cualquier zona) sin workers → nada que resolver
    return (
        False, [], "NO_WORKERS_AVAILABLE",
        "No se recibió inventario de workers para el placement.",
    )
