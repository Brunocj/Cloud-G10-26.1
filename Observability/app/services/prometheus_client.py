import httpx
import logging
from typing import Optional
from app.config import PROMETHEUS_URL

logger = logging.getLogger("observability.prometheus")


async def query_instant(promql: str) -> Optional[list]:
    """Execute instant PromQL query. Returns list of results or None on error."""
    url = f"{PROMETHEUS_URL}/api/v1/query"
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(url, params={"query": promql})
            resp.raise_for_status()
            data = resp.json()
            if data.get("status") == "success":
                return data["data"]["result"]
            logger.warning("Prometheus query returned non-success: %s", data)
            return None
    except Exception as e:
        logger.error("Prometheus query failed [%s]: %s", promql, e)
        return None


async def check_connectivity(target: str) -> bool:
    """Check if a Prometheus target is reachable via the targets API."""
    url = f"{PROMETHEUS_URL}/api/v1/targets"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url)
            data = resp.json()
            active = data.get("data", {}).get("activeTargets", [])
            for t in active:
                if target in t.get("scrapeUrl", "") or \
                   target in t.get("labels", {}).get("instance", ""):
                    return t.get("health") == "up"
            return False
    except Exception:
        return False


async def get_worker_cpu_usage(instance: str) -> Optional[float]:
    """
    Current CPU usage fraction of the worker host.
    Returns a value between 0.0 and 1.0.
    Uses node_exporter metrics.
    """
    promql = (
        f'1 - avg(rate(node_cpu_seconds_total'
        f'{{mode="idle",instance="{instance}"}}[5m]))'
    )
    results = await query_instant(promql)
    if results:
        return float(results[0]["value"][1])
    return None


async def get_worker_ram_usage(instance: str) -> Optional[float]:
    """
    Current RAM usage fraction of the worker host.
    Returns a value between 0.0 and 1.0.
    Uses node_exporter metrics.
    """
    promql = (
        f'1 - (node_memory_MemAvailable_bytes{{instance="{instance}"}}'
        f' / node_memory_MemTotal_bytes{{instance="{instance}"}})'
    )
    results = await query_instant(promql)
    if results:
        return float(results[0]["value"][1])
    return None


async def get_worker_cpu_cores_used(instance: str) -> Optional[float]:
    """
    Absolute CPU cores used on the worker host.
    = total_cores × usage_fraction
    Uses node_exporter metrics.
    """
    promql = (
        f'count(node_cpu_seconds_total{{mode="idle",instance="{instance}"}}) '
        f'* (1 - avg(rate(node_cpu_seconds_total{{mode="idle",instance="{instance}"}}[5m])))'
    )
    results = await query_instant(promql)
    if results:
        return float(results[0]["value"][1])
    return None


async def get_worker_ram_used_gb(instance: str) -> Optional[float]:
    """
    Absolute RAM used on the worker host in GB.
    Uses node_exporter metrics.
    """
    promql = (
        f'(node_memory_MemTotal_bytes{{instance="{instance}"}}'
        f' - node_memory_MemAvailable_bytes{{instance="{instance}"}}) / 1073741824'
    )
    results = await query_instant(promql)
    if results:
        return float(results[0]["value"][1])
    return None


async def get_worker_has_data(instance: str) -> bool:
    """Returns True if node_exporter is reporting data for this worker."""
    promql = f'node_cpu_seconds_total{{mode="idle",instance="{instance}"}}'
    results = await query_instant(promql)
    return bool(results)
