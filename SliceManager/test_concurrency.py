"""
Test de concurrencia para el Slice Manager — PUCP Cloud Orchestrator
=====================================================================
Caso de prueba: 1.4.2 CONCURRENT_REQUEST

Verifica que el sistema maneja 20 solicitudes simultáneas de despliegue
sin race conditions en la asignación de VLANs y puertos VNC, gracias a
la serialización garantizada por la asyncio.Queue del Slice Manager.

Modos de operación:
  --mode create   : Crea N drafts nuevos y los despliega (default)
  --mode existing : Usa una lista de slice_ids ya existentes en DRAFT

Fases:
  1. Autenticación contra Keycloak (opcional si JWT_ENABLED=false)
  2. Preparación de drafts (creación o validación de existentes)
  3. Disparo simultáneo de N requests de deploy vía asyncio.gather
  4. Verificación de unicidad de VLANs y VNC ports en MySQL

Uso:
  # Modo default: crea 10 drafts y los despliega
  python test_concurrency.py

  # Usar drafts existentes
  python test_concurrency.py --mode existing --slice-ids 101,102,103,104

  # Ajustar cantidad
  python test_concurrency.py --num 30

Dependencias:
  pip install httpx pymysql
"""

import argparse
import asyncio
import json
import sys
import time
from datetime import datetime

import httpx

try:
    import pymysql
    HAS_MYSQL = True
except ImportError:
    HAS_MYSQL = False

# ── Configuración ────────────────────────────────────────────────────────────

BASE_URL = "http://10.20.11.212:8085"          # API Gateway
KEYCLOAK_URL = "http://10.20.11.212:8086"      # Keycloak (para obtener JWT)
KEYCLOAK_REALM = "pucp-cloud"
KEYCLOAK_CLIENT = "pucp-cloud-webapp"

# Credenciales del usuario que ejecuta el test.
# Debe existir en Keycloak y tener permisos para deploy.
TEST_USER = "admin.pucp"
TEST_PASSWORD = "Admin123!"

# Zona y parámetros de deploy
AVAILABILITY_ZONE_ID = 1                    # 1 = Linux Cluster, 2 = OpenStack
TTL_HOURS = 1
IMAGE_ID = 1                                # image_id existente en tabla `images`

# MySQL (para verificación post-test)
MYSQL_CONFIG = {
    "host": "10.20.11.212",
    "port": 3306,
    "user": "mandarina",
    "password": "sandia",
    "database": "cloud",
}

# Topología mínima válida para crear borradores
DRAFT_TOPOLOGY = {
    "nodes": [
        {
            "id": "n-test-1",
            "type": "vm",
            "label": "VM-TestConc-1",
            "vcores": 1,
            "ram": 256,
            "disk": 1,
            "image_id": IMAGE_ID,
        }
    ],
    "edges": [],
}


# ── Autenticación ────────────────────────────────────────────────────────────

async def get_jwt_token(client: httpx.AsyncClient) -> str | None:
    """Obtiene un JWT de Keycloak. Si falla, retorna None y el test corre sin auth."""
    url = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}/protocol/openid-connect/token"
    data = {
        "grant_type": "password",
        "client_id": KEYCLOAK_CLIENT,
        "username": TEST_USER,
        "password": TEST_PASSWORD,
    }
    try:
        r = await client.post(url, data=data, timeout=10.0)
        if r.status_code == 200:
            token = r.json().get("access_token")
            print(f"  ✅ Token obtenido para '{TEST_USER}' (JWT len={len(token)})")
            return token
        print(f"  ⚠️  Keycloak respondió {r.status_code} — {r.text[:120]}")
        print("     Continuando sin token (asumiendo JWT_ENABLED=false en el gateway).")
        return None
    except Exception as e:
        print(f"  ⚠️  No se pudo contactar Keycloak: {e}")
        print("     Continuando sin token (asumiendo JWT_ENABLED=false).")
        return None


def auth_headers(token: str | None) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


# ── Helpers ──────────────────────────────────────────────────────────────────

async def create_draft(
    client: httpx.AsyncClient, token: str | None, request_num: int
) -> int | None:
    """Crea un borrador y devuelve su slice_id."""
    payload = {
        "name": f"concurrency-{request_num}-{int(time.time())}",
        "slice_json": DRAFT_TOPOLOGY,
    }
    try:
        r = await client.post(
            f"{BASE_URL}/api/v1/slices/draft",
            json=payload,
            headers=auth_headers(token),
            timeout=15.0,
        )
        if r.status_code in (200, 201):
            slice_id = r.json().get("slice_id")
            print(f"  [Setup #{request_num:02d}] Draft creado → slice_id={slice_id}")
            return slice_id
        print(f"  [Setup #{request_num:02d}] Error: {r.status_code} — {r.text[:120]}")
        return None
    except Exception as e:
        print(f"  [Setup #{request_num:02d}] Excepción: {e}")
        return None


async def deploy_slice(
    client: httpx.AsyncClient,
    token: str | None,
    slice_id: int,
    request_num: int,
    t0: float,
    results: list,
    delay=0,
) -> None:
    """Envía un request de deploy y registra la respuesta."""
    await asyncio.sleep(delay)
    payload = {
        "availability_zone_id": AVAILABILITY_ZONE_ID,
        "ttl_hours": TTL_HOURS,
        "motivo": "Test de concurrencia 1.4.2",
        "project_id": None,
    }
    start = time.perf_counter()
    offset_ms = round((start - t0) * 1000, 2)
    try:
        r = await client.post(
            f"{BASE_URL}/api/v1/slices/{slice_id}/deploy",
            json=payload,
            headers=auth_headers(token),
            timeout=10.0,
        )
        elapsed = time.perf_counter() - start
        result = {
            "request_num": request_num,
            "slice_id": slice_id,
            "status_code": r.status_code,
            "offset_ms": offset_ms,
            "elapsed_ms": round(elapsed * 1000, 1),
            "success": r.status_code == 202,
            "body": r.text[:80],
        }
        results.append(result)
        icon = "✅" if r.status_code == 202 else "❌"
        print(
            f"  {icon} #{request_num:02d} | slice={slice_id} | HTTP {r.status_code} "
            f"| t+{offset_ms:.1f}ms | {elapsed*1000:.1f}ms"
        )
    except Exception as e:
        elapsed = time.perf_counter() - start
        results.append({
            "request_num": request_num,
            "slice_id": slice_id,
            "status_code": "ERROR",
            "offset_ms": offset_ms,
            "elapsed_ms": round(elapsed * 1000, 1),
            "success": False,
            "body": str(e),
        })
        print(f"  💥 #{request_num:02d} | slice={slice_id} | ERROR: {e}")


# ── Verificación en MySQL ────────────────────────────────────────────────────

def verify_mysql_uniqueness(slice_ids: list) -> None:
    """
    Corre las queries de unicidad del caso 1.4.2:
      - VLANs duplicadas en la tabla `vlans`
      - VNC ports duplicados por worker en la tabla `vms`
      - Conteo de slices por estado final
    """
    if not HAS_MYSQL:
        print("\n  ⚠️  pymysql no está instalado. Skip verificación en MySQL.")
        print("      Instala con: pip install pymysql")
        return

    print("\n  Conectando a MySQL para verificación de unicidad...")
    try:
        conn = pymysql.connect(**MYSQL_CONFIG, cursorclass=pymysql.cursors.DictCursor)
    except Exception as e:
        print(f"  ❌ No se pudo conectar a MySQL: {e}")
        print(f"     Ajusta MYSQL_CONFIG en el script si es necesario.")
        return

    with conn.cursor() as cur:
        # ── Q1: VLANs duplicadas globalmente ──
        cur.execute("""
            SELECT id, COUNT(*) as n
            FROM vlans
            GROUP BY id
            HAVING COUNT(*) > 1
        """)
        vlan_dups = cur.fetchall()

        # ── Q2: VNC ports duplicados por worker (solo VMs activas) ──
        cur.execute("""
            SELECT worker_id, vnc_port, COUNT(*) as n
            FROM vms
            WHERE state = 'ACTIVE'
            GROUP BY worker_id, vnc_port
            HAVING COUNT(*) > 1
        """)
        vnc_dups = cur.fetchall()

        # ── Q3: Estados finales de los slices de este test ──
        if slice_ids:
            placeholders = ",".join(["%s"] * len(slice_ids))
            cur.execute(
                f"SELECT status, COUNT(*) as n FROM slices "
                f"WHERE id IN ({placeholders}) GROUP BY status",
                slice_ids,
            )
            states = cur.fetchall()
        else:
            states = []

    conn.close()

    print("\n" + "─" * 65)
    print("  VERIFICACIÓN EN MYSQL")
    print("─" * 65)

    # VLANs
    if not vlan_dups:
        print("  ✅ No hay VLANs duplicadas en tabla `vlans`.")
    else:
        print(f"  ❌ VLANs DUPLICADAS DETECTADAS ({len(vlan_dups)}):")
        for row in vlan_dups:
            print(f"       vlan_id={row['id']} aparece {row['n']} veces")

    # VNC ports
    if not vnc_dups:
        print("  ✅ No hay VNC ports duplicados por worker en tabla `vms`.")
    else:
        print(f"  ❌ VNC PORTS DUPLICADOS DETECTADOS ({len(vnc_dups)}):")
        for row in vnc_dups:
            print(
                f"       worker_id={row['worker_id']} vnc_port={row['vnc_port']} "
                f"aparece {row['n']} veces"
            )

    # Estados
    if states:
        print("\n  Estados finales de los slices del test:")
        for row in states:
            print(f"       {row['status']:20s} → {row['n']} slice(s)")
        transient = [
            r for r in states
            if r["status"] not in ("ACTIVE", "FAILED", "TERMINATED")
        ]
        if transient:
            print(
                f"\n  ⚠️  {sum(r['n'] for r in transient)} slice(s) en estado "
                f"transitorio. Espera 1-2 min y vuelve a correr la query."
            )
        else:
            print("  ✅ Todos los slices en estado terminal (ACTIVE / FAILED).")

    print("─" * 65)


# ── Resumen ──────────────────────────────────────────────────────────────────

def print_summary(results: list, total_elapsed: float) -> None:
    successful = [r for r in results if r["success"]]
    failed = [r for r in results if not r["success"]]
    times = [r["elapsed_ms"] for r in results if isinstance(r["elapsed_ms"], (int, float))]
    offsets = [r["offset_ms"] for r in results if isinstance(r["offset_ms"], (int, float))]

    print("\n" + "=" * 65)
    print("  RESUMEN DEL TEST 1.4.2 CONCURRENT_REQUEST")
    print("=" * 65)
    print(f"  Requests enviados:          {len(results)}")
    print(f"  Respuestas 202 Accepted:    {len(successful)}")
    print(f"  Fallos:                     {len(failed)}")
    print(f"  Tiempo total del test:      {total_elapsed*1000:.1f}ms")
    if times:
        print(f"  Latencia mín / prom / máx:  "
              f"{min(times):.1f}ms / {sum(times)/len(times):.1f}ms / {max(times):.1f}ms")
    if offsets:
        spread = max(offsets) - min(offsets)
        print(f"  Ventana de disparo:         {spread:.2f}ms "
              f"(min={min(offsets):.2f}, max={max(offsets):.2f})")
    print("=" * 65)

    if failed:
        print("\n  Requests fallidos:")
        for r in failed:
            print(f"    #{r['request_num']:02d} slice={r['slice_id']} → "
                  f"{r['status_code']} | {r['body']}")

    all_under_1s = all(r["elapsed_ms"] < 1000 for r in successful)
    all_202 = len(successful) == len(results)
    print()
    if all_202 and all_under_1s:
        print("  ✅ PASS — Los N requests recibieron 202 Accepted en menos de 1s.")
        print("     El placement_worker procesará la cola de forma serial.")
    elif all_202:
        print("  ⚠️  PARCIAL — Todos respondieron 202 pero algún request tardó >1s.")
    else:
        print(f"  ❌ FAIL — {len(failed)} request(s) no recibieron 202. Revisar logs.")


# ── Main ─────────────────────────────────────────────────────────────────────

async def main(args):
    print("=" * 65)
    print("  PUCP Cloud — Test 1.4.2 CONCURRENT_REQUEST")
    print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 65)
    print(f"  API Gateway:          {BASE_URL}")
    print(f"  Modo:                 {args.mode}")
    print(f"  Requests simultáneos: {args.num}")
    print(f"  Availability zone:    {AVAILABILITY_ZONE_ID}")
    print("=" * 65)

    async with httpx.AsyncClient() as client:

        # ── Fase 1: Conectividad ─────────────────────────────────────────────
        print("\n[1/4] Verificando conectividad con el API Gateway...")
        try:
            r = await client.get(f"{BASE_URL}/health", timeout=5.0)
            print(f"  ✅ Gateway responde: {r.status_code}")
        except Exception as e:
            print(f"  ❌ No se puede conectar al gateway: {e}")
            sys.exit(1)

        # ── Fase 2: Autenticación ────────────────────────────────────────────
        print("\n[2/4] Obteniendo token de Keycloak...")
        token = await get_jwt_token(client)

        # ── Fase 3: Preparar slice_ids ───────────────────────────────────────
        if args.mode == "existing":
            if not args.slice_ids:
                print("\n  ❌ Modo 'existing' requiere --slice-ids")
                sys.exit(1)
            slice_ids = [int(x) for x in args.slice_ids.split(",")]
            print(f"\n[3/4] Usando {len(slice_ids)} slice_ids existentes: {slice_ids}")
        else:
            print(f"\n[3/4] Creando {args.num} drafts...")
            draft_tasks = [
                create_draft(client, token, i + 1) for i in range(args.num)
            ]
            created = await asyncio.gather(*draft_tasks)
            slice_ids = [sid for sid in created if sid is not None]
            if not slice_ids:
                print("\n  ❌ No se pudo crear ningún draft. Abortando.")
                sys.exit(1)
            print(f"\n  ✅ {len(slice_ids)}/{args.num} drafts creados.")

        # ── Fase 4: Disparo simultáneo ───────────────────────────────────────
        print(f"\n[4/4] Disparando {len(slice_ids)} deploys SIMULTÁNEAMENTE...")
        print("      (asyncio.gather los lanza en el mismo tick del event loop)\n")

        results = []
        t_start = time.perf_counter()
        t0 = t_start  # anclado para calcular offset de cada request

        deploy_tasks = [
            deploy_slice(client, token, sid, i + 1, t0, results, delay=0.025)
            for i, sid in enumerate(slice_ids)
        ]
        await asyncio.gather(*deploy_tasks)
        total_elapsed = time.perf_counter() - t_start

    # ── Resumen y verificación ───────────────────────────────────────────────
    print_summary(results, total_elapsed)

    print("\n  Esperando 3 minutos para que el placement_worker procese la cola")
    print("  y las VMs terminen de aprovisionar...")
    if args.skip_wait:
        print("  (skip-wait activo, saltando espera)")
    else:
        for remaining in range(180, 0, -10):
            print(f"    {remaining}s restantes...", end="\r")
            await asyncio.sleep(10)
        print()

    verify_mysql_uniqueness(slice_ids)

    print("\n  Nota: Los slices creados quedaron desplegados. Para limpiarlos:")
    for sid in slice_ids:
        print(f"    curl -X DELETE {BASE_URL}/api/v1/slices/{sid} "
              f"-H 'Authorization: Bearer <token>'")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["create", "existing"], default="create",
                   help="create=crea drafts nuevos; existing=usa slice_ids ya en DRAFT")
    p.add_argument("--num", type=int, default=10,
                   help="Número de requests simultáneos (modo create)")
    p.add_argument("--slice-ids", type=str, default=None,
                   help="Lista de slice_ids separados por coma (modo existing)")
    p.add_argument("--skip-wait", action="store_true",
                   help="No esperar 2 min antes de verificar en MySQL")
    return p.parse_args()


if __name__ == "__main__":
    asyncio.run(main(parse_args()))
