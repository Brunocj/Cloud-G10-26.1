"""
weight_updater.py — Observability scheduler

Dos modos de operación por worker:

MODO BASELINE (sin VMs activas):
  - Acumula Welford del consumo del host sin carga de VMs
  - _baseline[worker_id] → μ_base_cpu, μ_base_ram
  - Se activa automáticamente cuando un worker no tiene VMs ACTIVE

MODO NORMAL (con VMs activas):
  - Resta el baseline del consumo real → consumo neto atribuible a VMs
  - Calcula ratio = consumo_neto / nominal_solicitado
  - Welford corre sobre el ratio → μ_ratio, σ_ratio
  - OC_r[j] = 1 / (μ_ratio_r[j] + k_r · σ_ratio_r[j])

Ambos estados viven en memoria — se pierden al reiniciar el container.
"""

import logging
import math
from collections import deque
from typing import Dict, Optional, Tuple

from sqlalchemy import func

from app.config import (
    K_CPU, K_RAM,
    OC_CPU_DEFAULT, OC_RAM_DEFAULT, OC_DISK_DEFAULT,
    OC_RAM_MAX, OC_CPU_MAX,
    WINDOW_SIZE,
)
from app.database import SessionLocal, Worker, Vm
from app.targets import get_node_target, get_targets
from app.services.prometheus_client import (
    get_worker_cpu_cores_used,
    get_worker_ram_used_gb,
    get_worker_has_data,
)

logger = logging.getLogger("observability.weight_updater")

# Welford state on efficiency ratios (mode: normal)
# { worker_id: { buf_cpu, mu_cpu, M2_cpu, buf_ram, mu_ram, M2_ram } }
_state: Dict[int, Dict] = {}

# Welford state on raw host usage (mode: baseline)
# { worker_id: { buf_cpu, mu_cpu, M2_cpu, buf_ram, mu_ram, M2_ram } }
_baseline: Dict[int, Dict] = {}


def _welford_add(buf: deque, mu: float, M2: float, x_new: float) -> tuple:
    """Welford sliding window update — O(1)."""
    n = len(buf)
    if n < buf.maxlen:
        buf.append(x_new)
        n_new = len(buf)
        delta = x_new - mu
        mu_new = mu + delta / n_new
        delta2 = x_new - mu_new
        M2_new = M2 + delta * delta2
    else:
        x_old = buf[0]
        buf.append(x_new)
        n = buf.maxlen
        mu_new = mu + (x_new - x_old) / n
        M2_new = M2 + (x_new - x_old) * (x_new - mu_new + x_old - mu)
    return mu_new, max(M2_new, 0.0)


def _sigma(M2: float, n: int) -> float:
    if n < 2:
        return 0.0
    return math.sqrt(M2 / n)


def _init_state() -> Dict:
    return {
        "buf_cpu": deque(maxlen=WINDOW_SIZE),
        "mu_cpu":  0.0,
        "M2_cpu":  0.0,
        "buf_ram": deque(maxlen=WINDOW_SIZE),
        "mu_ram":  0.0,
        "M2_ram":  0.0,
    }


def _get_active_worker_ids(db) -> set:
    rows = db.query(Vm.worker_id).filter(
        Vm.state == "ACTIVE",
        Vm.worker_id.isnot(None)
    ).distinct().all()
    return {r[0] for r in rows}


def _get_all_worker_ids(db) -> set:
    rows = db.query(Worker.id).all()
    return {r[0] for r in rows}


def _get_nominal_usage(db, worker_id: int) -> Tuple[float, float]:
    nominal_cpu = db.query(func.sum(Vm.vcore)).filter(
        Vm.worker_id == worker_id,
        Vm.state == "ACTIVE"
    ).scalar() or 0

    nominal_ram_mb = db.query(func.sum(Vm.ram)).filter(
        Vm.worker_id == worker_id,
        Vm.state == "ACTIVE"
    ).scalar() or 0

    return float(nominal_cpu), float(nominal_ram_mb) / 1024.0


async def run_update_cycle():
    db = SessionLocal()
    try:
        active_worker_ids = _get_active_worker_ids(db)
        all_worker_ids    = _get_all_worker_ids(db)
        idle_worker_ids   = all_worker_ids - active_worker_ids

        logger.info(
            "[cycle] Workers: %d with VMs, %d idle (baseline mode)",
            len(active_worker_ids), len(idle_worker_ids)
        )

        # ── BASELINE MODE — workers without VMs ──────────────────────────────
        for worker_id in idle_worker_ids:
            instance = get_node_target(worker_id)
            if not instance:
                continue
            if not await get_worker_has_data(instance):
                continue

            cpu_used = await get_worker_cpu_cores_used(instance)
            ram_used = await get_worker_ram_used_gb(instance)

            if cpu_used is None or ram_used is None:
                continue

            if worker_id not in _baseline:
                _baseline[worker_id] = _init_state()

            b = _baseline[worker_id]
            b["mu_cpu"], b["M2_cpu"] = _welford_add(b["buf_cpu"], b["mu_cpu"], b["M2_cpu"], cpu_used)
            b["mu_ram"], b["M2_ram"] = _welford_add(b["buf_ram"], b["mu_ram"], b["M2_ram"], ram_used)

            n = len(b["buf_cpu"])
            logger.debug(
                "[worker=%d][baseline] n=%d μ_cpu=%.4f μ_ram=%.4fGB",
                worker_id, n, b["mu_cpu"], b["mu_ram"]
            )

        # ── NORMAL MODE — workers with VMs ────────────────────────────────────
        updated = 0
        for worker_id in active_worker_ids:
            instance = get_node_target(worker_id)
            if not instance:
                logger.warning("[worker=%d] No node_exporter target — skipping", worker_id)
                continue

            if not await get_worker_has_data(instance):
                logger.warning("[worker=%d] No node_exporter data — skipping", worker_id)
                continue

            cpu_used = await get_worker_cpu_cores_used(instance)
            ram_used = await get_worker_ram_used_gb(instance)

            if cpu_used is None or ram_used is None:
                logger.warning("[worker=%d] Missing Prometheus data — skipping", worker_id)
                continue

            nominal_cpu, nominal_ram_gb = _get_nominal_usage(db, worker_id)
            if nominal_cpu <= 0 or nominal_ram_gb <= 0:
                logger.warning("[worker=%d] No nominal usage — skipping", worker_id)
                continue

            # Subtract baseline if available
            base = _baseline.get(worker_id)
            if base and len(base["buf_cpu"]) >= 5:
                net_cpu = max(cpu_used - base["mu_cpu"], 0.0)
                net_ram = max(ram_used - base["mu_ram"], 0.0)
                logger.debug(
                    "[worker=%d] baseline=(%.4f cores, %.4fGB) net=(%.4f, %.4f)",
                    worker_id, base["mu_cpu"], base["mu_ram"], net_cpu, net_ram
                )
            else:
                # No baseline yet — use raw usage, note it in log
                net_cpu = cpu_used
                net_ram = ram_used
                logger.info(
                    "[worker=%d] No baseline yet (n=%d) — using raw usage",
                    worker_id, len(base["buf_cpu"]) if base else 0
                )

            ratio_cpu = net_cpu / nominal_cpu

            # Cap ratio at 1.0 — if net > nominal, no overcommit possible
            ratio_cpu = min(ratio_cpu, 1.0)

            if worker_id not in _state:
                _state[worker_id] = _init_state()

            s = _state[worker_id]
            s["mu_cpu"], s["M2_cpu"] = _welford_add(s["buf_cpu"], s["mu_cpu"], s["M2_cpu"], ratio_cpu)

            n = len(s["buf_cpu"])
            sig_cpu = _sigma(s["M2_cpu"], n)

            denom_cpu = s["mu_cpu"] + K_CPU * sig_cpu

            oc_cpu = min(1.0 / denom_cpu, OC_CPU_MAX) if denom_cpu > 0 else OC_CPU_DEFAULT
            oc_ram = 2.0  # Fixed — RAM overcommit disabled, always 2.0

            worker = db.query(Worker).filter(Worker.id == worker_id).first()
            if not worker:
                continue

            worker.oc_cpu   = oc_cpu
            worker.oc_ram   = oc_ram
            worker.oc_disco = OC_DISK_DEFAULT

            logger.info(
                "[worker=%d] OC → oc_cpu=%.3f oc_ram=%.3f (fixed) "
                "(ratio_cpu=%.4f σ=%.4f "
                "net_cpu=%.3f net_ram=%.3fGB nominal_cpu=%.0f nominal_ram=%.2fGB n=%d)",
                worker_id, oc_cpu, oc_ram,
                s["mu_cpu"], sig_cpu,
                net_cpu, net_ram, nominal_cpu, nominal_ram_gb, n
            )
            updated += 1

        db.commit()
        logger.info("[cycle] Completed — %d workers updated, %d in baseline mode",
                    updated, len(idle_worker_ids))

    except Exception as e:
        logger.error("[cycle] Error: %s", e)
        db.rollback()
    finally:
        db.close()


def get_window_state() -> Dict:
    result = {}
    for worker_id, s in _state.items():
        n = len(s["buf_cpu"])
        b = _baseline.get(worker_id, {})
        nb = len(b["buf_cpu"]) if b else 0
        result[str(worker_id)] = {
            "n_samples":       n,
            "window_size":     WINDOW_SIZE,
            "window_pct":      round(n / WINDOW_SIZE * 100, 1),
            "mu_ratio_cpu":    round(s["mu_cpu"], 4),
            "sigma_ratio_cpu": round(_sigma(s["M2_cpu"], n), 4),
            "mu_ratio_ram":    "fixed",
            "sigma_ratio_ram": "fixed",
            "oc_ram":          2.0,
            "baseline_n":      nb,
            "baseline_mu_cpu": round(b["mu_cpu"], 4) if b else 0,
            "baseline_mu_ram": round(b["mu_ram"], 4) if b else 0,
        }

    # Also include workers only in baseline mode
    for worker_id, b in _baseline.items():
        if str(worker_id) not in result:
            nb = len(b["buf_cpu"])
            result[str(worker_id)] = {
                "n_samples":       0,
                "window_size":     WINDOW_SIZE,
                "window_pct":      0,
                "mu_ratio_cpu":    0,
                "sigma_ratio_cpu": 0,
                "mu_ratio_ram":    0,
                "sigma_ratio_ram": 0,
                "baseline_n":      nb,
                "baseline_mu_cpu": round(b["mu_cpu"], 4),
                "baseline_mu_ram": round(b["mu_ram"], 4),
            }
    return result
