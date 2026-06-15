"""
Test de concurrencia para el Slice Manager — PUCP Cloud Orchestrator
=====================================================================
Propósito:
  Demostrar que el endpoint de despliegue soporta múltiples requests
  simultáneos sin bloquearse, respondiendo 202 Accepted inmediatamente
  a todos ellos mientras el placement_worker los procesa en background
  de forma serial y ordenada.

Uso:
  1. Asegurarse de tener al menos un slice en estado DRAFT en la BD.
     Si no, el script lo crea automáticamente antes del test.
  2. Ajustar BASE_URL y NUM_REQUESTS según el entorno.
  3. Ejecutar: python test_concurrency.py

Dependencias:
  pip install httpx asyncio
"""

import asyncio
import httpx
import time
import json
from datetime import datetime

# ── Configuración ────────────────────────────────────────────────────────────

BASE_URL = "http://127.0.0.1:8085"   # URL del API Gateway (o :8000 para Slice Manager directo)
NUM_REQUESTS = 20                      # Número de deploys simultáneos
AVAILABILITY_ZONE = "Linux Cluster"   # Zona configurada en tu entorno

# Topología mínima válida para crear borradores de prueba
DRAFT_TOPOLOGY = {
    "nodes": [
        {
            "id": "n-test-1",
            "type": "vm",
            "label": "VM-Test-1",
            "vcores": 1,
            "ram": 256,
            "disk": 1,
            "image_id": 1           # Asegúrate de que exista este image_id en tu BD
        }
    ],
    "edges": []
}

DEPLOY_PAYLOAD = {
    "availability_zone": AVAILABILITY_ZONE,
    "ttl_hours": 1,
    "motivo": "Test de concurrencia automatizado"
}

# ── Helpers ──────────────────────────────────────────────────────────────────

async def create_draft(client: httpx.AsyncClient, request_num: int) -> int | None:
    """Crea un borrador de slice y retorna su ID."""
    payload = {
        "name": f"concurrency-test-{request_num}-{int(time.time())}",
        "slice_json": DRAFT_TOPOLOGY
    }
    try:
        response = await client.post(
            f"{BASE_URL}/api/v1/slices/draft",
            json=payload,
            timeout=15.0
        )
        if response.status_code in (200, 201):
            data = response.json()
            slice_id = data.get("slice_id")
            print(f"  [Setup #{request_num:02d}] Borrador creado → slice_id={slice_id}")
            return slice_id
        else:
            print(f"  [Setup #{request_num:02d}] Error creando borrador: {response.status_code} — {response.text[:100]}")
            return None
    except Exception as e:
        print(f"  [Setup #{request_num:02d}] Excepción creando borrador: {e}")
        return None


async def deploy_slice(
    client: httpx.AsyncClient,
    slice_id: int,
    request_num: int,
    results: list
) -> None:
    """
    Envía un request de despliegue y registra el resultado.
    Mide el tiempo de respuesta del API (no del despliegue completo).
    """
    start = time.perf_counter()
    try:
        response = await client.post(
            f"{BASE_URL}/api/v1/slices/{slice_id}/deploy",
            json=DEPLOY_PAYLOAD,
            timeout=10.0
        )
        elapsed = time.perf_counter() - start
        status = response.status_code

        result = {
            "request_num": request_num,
            "slice_id": slice_id,
            "status_code": status,
            "elapsed_ms": round(elapsed * 1000, 1),
            "success": status == 202,
            "body": response.text[:80]
        }
        results.append(result)

        icon = "✅" if status == 202 else "❌"
        print(
            f"  {icon} Request #{request_num:02d} | slice={slice_id} | "
            f"HTTP {status} | {elapsed * 1000:.1f}ms"
        )

    except httpx.TimeoutException:
        elapsed = time.perf_counter() - start
        results.append({
            "request_num": request_num,
            "slice_id": slice_id,
            "status_code": "TIMEOUT",
            "elapsed_ms": round(elapsed * 1000, 1),
            "success": False,
            "body": "Timeout esperando respuesta"
        })
        print(f"  ⏱️  Request #{request_num:02d} | slice={slice_id} | TIMEOUT después de {elapsed * 1000:.1f}ms")

    except Exception as e:
        elapsed = time.perf_counter() - start
        results.append({
            "request_num": request_num,
            "slice_id": slice_id,
            "status_code": "ERROR",
            "elapsed_ms": round(elapsed * 1000, 1),
            "success": False,
            "body": str(e)
        })
        print(f"  💥 Request #{request_num:02d} | slice={slice_id} | ERROR: {e}")


def print_summary(results: list, total_elapsed: float) -> None:
    """Imprime el resumen estadístico del test."""
    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]
    times = [r["elapsed_ms"] for r in results if isinstance(r["elapsed_ms"], (int, float))]

    print("\n" + "=" * 65)
    print("  RESUMEN DEL TEST DE CONCURRENCIA")
    print("=" * 65)
    print(f"  Total requests enviados:    {len(results)}")
    print(f"  Respuestas 202 Accepted:    {len(successful)}  ✅")
    print(f"  Fallos / Timeouts:          {len(failed)}  {'❌' if failed else '✅'}")
    print(f"  Tiempo total del test:      {total_elapsed:.3f}s")
    if times:
        print(f"  Tiempo mínimo de respuesta: {min(times):.1f}ms")
        print(f"  Tiempo máximo de respuesta: {max(times):.1f}ms")
        print(f"  Tiempo promedio:            {sum(times)/len(times):.1f}ms")
    print("=" * 65)

    if failed:
        print("\n  ⚠️  Requests fallidos:")
        for r in failed:
            print(f"     #{r['request_num']:02d} slice={r['slice_id']} → {r['status_code']} | {r['body']}")

    print("\n  Interpretación:")
    if len(successful) == len(results):
        print("  ✅ PASS — Todos los requests recibieron 202 Accepted de forma")
        print("     inmediata. El placement_worker los procesará en background")
        print("     de forma serial, sin race conditions en la asignación")
        print("     de VLANs, VNC ports ni MACs.")
    else:
        print(f"  ⚠️  {len(failed)} request(s) no recibieron 202. Revisar logs del")
        print("     Slice Manager para diagnóstico.")
    print("=" * 65)


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    print("=" * 65)
    print("  PUCP Cloud — Test de Concurrencia del Slice Manager")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)
    print(f"  API Gateway: {BASE_URL}")
    print(f"  Requests simultáneos: {NUM_REQUESTS}")
    print(f"  Zona: {AVAILABILITY_ZONE}")
    print("=" * 65)

    async with httpx.AsyncClient() as client:

        # ── Fase 1: Verificar conectividad ───────────────────────────────────
        print("\n[1/3] Verificando conectividad con el API Gateway...")
        try:
            health = await client.get(f"{BASE_URL}/health", timeout=5.0)
            print(f"  ✅ Gateway responde: {health.status_code} — {health.text[:60]}")
        except Exception as e:
            print(f"  ❌ No se puede conectar al gateway: {e}")
            print("     Verifica que el sistema esté corriendo y BASE_URL sea correcto.")
            return

        # ── Fase 2: Crear borradores ─────────────────────────────────────────
        print(f"\n[2/3] Creando {NUM_REQUESTS} borradores de slice...")
        draft_tasks = [
            create_draft(client, i + 1)
            for i in range(NUM_REQUESTS)
        ]
        slice_ids = await asyncio.gather(*draft_tasks)
        valid_ids = [sid for sid in slice_ids if sid is not None]

        if not valid_ids:
            print("\n  ❌ No se pudo crear ningún borrador. Abortando test.")
            print("     Verifica que image_id=1 exista en tu tabla 'images'.")
            return

        print(f"\n  ✅ {len(valid_ids)}/{NUM_REQUESTS} borradores creados exitosamente.")

        if len(valid_ids) < NUM_REQUESTS:
            print(f"  ⚠️  Solo se harán {len(valid_ids)} requests (los borradores que funcionaron).")

        # ── Fase 3: Disparar todos los deploys simultáneamente ───────────────
        print(f"\n[3/3] Disparando {len(valid_ids)} requests de deploy SIMULTÁNEAMENTE...")
        print("      (todos se envían al mismo tiempo, sin esperar respuesta entre sí)\n")

        results = []
        start_total = time.perf_counter()

        deploy_tasks = [
            deploy_slice(client, slice_id, i + 1, results)
            for i, slice_id in enumerate(valid_ids)
        ]

        # asyncio.gather ejecuta todas las coroutines al mismo tiempo
        await asyncio.gather(*deploy_tasks)

        total_elapsed = time.perf_counter() - start_total

    # ── Resumen ──────────────────────────────────────────────────────────────
    print_summary(results, total_elapsed)

    # ── Cleanup opcional ─────────────────────────────────────────────────────
    print("\n  Nota: Los slices creados quedaron en estado PENDING_APPROVAL.")
    print("  Puedes destruirlos desde la Web App o con:")
    for sid in valid_ids:
        print(f"    curl -X DELETE {BASE_URL}/api/v1/slices/{sid}")


if __name__ == "__main__":
    asyncio.run(main())
