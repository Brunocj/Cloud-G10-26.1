import httpx
import asyncio
import logging
import os

logger = logging.getLogger("SliceManager.Telemetry")

# Leemos la URL de Prometheus de las variables de entorno
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://10.0.10.1:9090")

# Este es tu inventario de servidores físicos (ajusta las IPs a tu laboratorio)
# app/telemetry.py

# 🔥 FIX: Cambiamos "server-1" por simplemente el ID entero 1
WORKERS_CONFIG = [
    {"worker_id": 1, "instance": "localhost:9100"},
    {"worker_id": 2, "instance": "10.0.10.2:9100"},
    {"worker_id": 3, "instance": "10.0.10.3:9100"},
    {"worker_id": 4, "instance": "10.0.10.4:9100"}
]

async def get_real_worker_metrics():
    """
    Consulta a Prometheus las métricas reales de los hipervisores.
    Retorna la lista de workers lista para el payload del VM Placement.
    """
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            async def fetch_metric(promql):
                resp = await client.get(f"{PROMETHEUS_URL}/api/v1/query", params={"query": promql})
                resp.raise_for_status()
                results = resp.json().get("data", {}).get("result", [])
                return {r["metric"]["instance"]: float(r["value"][1]) for r in results}

            # Lanzamos las 3 consultas simultáneamente (Concurrencia)
            ram_task  = fetch_metric("node_memory_MemAvailable_bytes/1024/1024")
            cpus_task = fetch_metric('count by(instance)(node_cpu_seconds_total{mode="idle"})')
            disk_task = fetch_metric('node_filesystem_avail_bytes{mountpoint="/"}/1024/1024/1024')

            # Esperamos a que las 3 terminen
            ram, cpus, disk = await asyncio.gather(ram_task, cpus_task, disk_task)

            # --- 🔍 DEBUGGING CLAVE ---
            # Esto imprimirá en tu terminal los nombres exactos que Prometheus está usando.
            logger.info(f"🔍 Nombres de instancias detectadas en Prometheus: {list(ram.keys())}")

            workers_payload = []
            for w in WORKERS_CONFIG:
                inst = w["instance"]
                workers_payload.append({
                    "worker_id": w["worker_id"],
                    "available_vcpus":   int(cpus.get(inst, 0)),
                    "available_ram_mb":  int(ram.get(inst, 0)),
                    "available_disk_gb": int(disk.get(inst, 0)),
                })
            
            logger.info(f"Métricas reales obtenidas de {len(workers_payload)} servidores.")
            return workers_payload

    except Exception as e:
        logger.error(f"Fallo al conectar con Prometheus: {str(e)}")
        # FALLBACK: Si Prometheus está caído, usamos datos simulados para no detener el sistema
        logger.warning("Usando métricas simuladas como respaldo...")
        return [
            { "worker_id": "server-1", "available_vcpus": 10, "available_ram_mb": 16000, "available_disk_gb": 500 },
            { "worker_id": "server-2", "available_vcpus": 10, "available_ram_mb": 16000, "available_disk_gb": 500 },
            { "worker_id": "server-3", "available_vcpus": 10, "available_ram_mb": 16000, "available_disk_gb": 500 },
            { "worker_id": "server-4", "available_vcpus": 10, "available_ram_mb": 16000, "available_disk_gb": 500 }
        ]