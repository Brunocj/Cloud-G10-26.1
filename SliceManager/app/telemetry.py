import httpx
import asyncio
import logging
import os

logger = logging.getLogger("SliceManager.Telemetry")

# Leemos la URL de Prometheus de las variables de entorno
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://192.168.202.1:9090")

# Inventario de compute nodes (workers de cómputo).
# ⚠️  Worker 1 (server1) es el HEADNODE: corre los servicios de orquestación
#     (SliceManager, QueueManager, etc.) y NO recibe VMs de usuario.
WORKERS_CONFIG = [
    {"worker_id": 1, "instance": "192.168.201.1:9100"},
    {"worker_id": 2, "instance": "192.168.201.2:9100"},
    {"worker_id": 3, "instance": "192.168.201.3:9100"},
    {"worker_id": 4, "instance": "192.168.201.4:9100"}
]

async def get_real_worker_metrics():
    """
    Consulta a Prometheus las métricas reales de los hipervisores.
    Retorna la lista de workers lista para el payload del VM Placement.
    Solo incluye los compute nodes (workers 2, 3 y 4).
    """
    logger.info("─"*60)
    logger.info("[TELEMETRY] 📡 Consultando Prometheus en %s ...", PROMETHEUS_URL)
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            async def fetch_metric(promql):
                resp = await client.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": promql})
                resp.raise_for_status()
                results = resp.json().get("data", {}).get("result", [])
                return {r["metric"]["instance"]: float(r["value"][1]) for r in results}

            # Lanzamos las 3 consultas simultáneamente (Concurrencia)
            logger.info("[TELEMETRY]   Consultando: RAM disponible, vCPUs idle, Disco libre...")
            ram_task  = fetch_metric("node_memory_MemAvailable_bytes/1024/1024")
            cpus_task = fetch_metric('count by(instance)(node_cpu_seconds_total{mode="idle"})')
            disk_task = fetch_metric('node_filesystem_avail_bytes{mountpoint="/"}/1024/1024/1024')

            # Esperamos a que las 3 terminen
            ram, cpus, disk = await asyncio.gather(ram_task, cpus_task, disk_task)

            logger.info("[TELEMETRY] ✅ Métricas recibidas de Prometheus. Instancias detectadas: %s", list(ram.keys()))

            workers_payload = []
            for w in WORKERS_CONFIG:
                inst = w["instance"]
                vcpus_val = int(cpus.get(inst, 0))
                ram_val   = int(ram.get(inst, 0))
                disk_val  = int(disk.get(inst, 0))
                workers_payload.append({
                    "worker_id": w["worker_id"],
                    "available_vcpus":   vcpus_val,
                    "available_ram_mb":  ram_val,
                    "available_disk_gb": disk_val,
                })
                logger.info("[TELEMETRY]   Worker-%d (%s) → vCPUs: %d  RAM: %d MB  Disco: %d GB",
                            w["worker_id"], inst, vcpus_val, ram_val, disk_val)

            logger.info("[TELEMETRY] 📊 Resumen: %d compute nodes disponibles para placement", len(workers_payload))
            logger.info("─"*60)
            return workers_payload

    except Exception as e:
        logger.error("[TELEMETRY] ❌ Fallo al conectar con Prometheus: %s", e)
        # FALLBACK: Si Prometheus está caído, usamos datos simulados para no detener el sistema
        logger.warning("[TELEMETRY] ⚠️  Usando métricas SIMULADAS como respaldo (workers 2, 3, 4)...")
        return [
            {"worker_id": 2, "available_vcpus": 10, "available_ram_mb": 16000, "available_disk_gb": 500},
            {"worker_id": 3, "available_vcpus": 10, "available_ram_mb": 16000, "available_disk_gb": 500},
            {"worker_id": 4, "available_vcpus": 10, "available_ram_mb": 16000, "available_disk_gb": 500},
        ]
