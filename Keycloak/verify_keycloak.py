#!/usr/bin/env python3
"""
verify_keycloak.py — Verifica que Keycloak esté levantado y operativo.

Uso:
    python verify_keycloak.py          # prueba contra localhost (modo local)
    python verify_keycloak.py --vm     # prueba contra 10.20.11.212 (modo VM)

Requiere: pip install requests
"""

import sys
import json
import argparse
from typing import Dict, Optional, Tuple
import requests

# ── Configuración de entornos ─────────────────────────────────────────────────
ENVIRONMENTS = {
    "local": {
        "base": "http://localhost:8086",
        "gateway": "http://localhost:8085",
        "label": "LOCAL (Docker Desktop / localhost)",
    },
    "vm": {
        "base": "http://10.20.11.212:8086",
        "gateway": "http://10.20.11.212:8085",
        "label": "VM (10.20.11.212)",
    },
}

REALM     = "pucp-cloud"
CLIENT_ID = "pucp-cloud-webapp"

# ── Usuarios de prueba con los roles actualizados ─────────────────────────────
TEST_USERS = [
    {"username": "superadmin.pucp",  "password": "Super123!",   "expected_role": "superAdmin"},
    {"username": "admin.pucp",       "password": "Admin123!",   "expected_role": "admin"},
    {"username": "jefe.garcia",      "password": "Jefe123!",    "expected_role": "jefeProyecto"},
    {"username": "usuario.lopez",    "password": "Usuario123!", "expected_role": "usuario"},
    {"username": "usuario.torres",   "password": "Usuario123!", "expected_role": "usuario"},
]

# Roles válidos del negocio (de menor a mayor prioridad)
KNOWN_ROLES = {"usuario", "jefeProyecto", "admin", "superAdmin"}


# ── Helpers ────────────────────────────────────────────────────────────────────

def get_token(base: str, username: str, password: str) -> Optional[Dict]:
    url  = f"{base}/realms/{REALM}/protocol/openid-connect/token"
    data = {
        "grant_type": "password",
        "client_id":  CLIENT_ID,
        "username":   username,
        "password":   password,
    }
    r = requests.post(url, data=data, timeout=10)
    return r.json() if r.status_code == 200 else None


def decode_payload(access_token: str) -> dict:
    import base64
    parts        = access_token.split(".")
    payload_b64  = parts[1] + "=="
    payload_bytes = base64.urlsafe_b64decode(payload_b64)
    return json.loads(payload_bytes)


# ── Pasos de verificación ─────────────────────────────────────────────────────

def check_health(base: str) -> bool:
    print("1️⃣  Verificando salud de Keycloak...")
    try:
        r = requests.get(f"{base}/health/ready", timeout=5)
        if r.status_code == 200:
            print(f"   ✅ Keycloak UP en {base}\n")
            return True
        print(f"   ❌ Status inesperado: {r.status_code}")
        return False
    except requests.exceptions.ConnectionError:
        print(f"   ❌ No se pudo conectar a {base}")
        print("      ¿Está corriendo el contenedor? → docker compose up -d")
        return False


def check_realm(base: str) -> bool:
    print(f"2️⃣  Verificando realm '{REALM}'...")
    r = requests.get(f"{base}/realms/{REALM}", timeout=5)
    if r.status_code == 200:
        data = r.json()
        print(f"   ✅ Realm: {data.get('realm')} ({data.get('displayName')})\n")
        return True
    print(f"   ❌ Realm no encontrado (status {r.status_code})")
    print("      Revisa que --import-realm esté en el command de Keycloak.")
    return False


def check_jwks(base: str) -> bool:
    jwks_url = f"{base}/realms/{REALM}/protocol/openid-connect/certs"
    print("3️⃣  Verificando JWKS (clave pública RSA)...")
    r = requests.get(jwks_url, timeout=5)
    if r.status_code == 200:
        keys = r.json().get("keys", [])
        print(f"   ✅ JWKS disponible — {len(keys)} clave(s) publicada(s)")
        print(f"   🔗 {jwks_url}\n")
        return True
    print(f"   ❌ JWKS no disponible (status {r.status_code})")
    return False


def check_users(base: str) -> Tuple[bool, Dict]:
    print("4️⃣  Verificando autenticación y roles de usuarios de prueba...")
    all_ok = True
    tokens = {}

    for user in TEST_USERS:
        token_data = get_token(base, user["username"], user["password"])
        if token_data is None:
            print(f"   ❌ {user['username']:<22} — fallo al obtener token")
            all_ok = False
            continue

        payload    = decode_payload(token_data["access_token"])
        all_roles  = payload.get("realm_access", {}).get("roles", [])
        user_roles = [r for r in all_roles if r in KNOWN_ROLES]
        sub        = payload.get("sub", "")

        if user["expected_role"] in user_roles:
            print(f"   ✅ {user['username']:<22} role={user_roles}  sub={sub[:8]}…")
            tokens[user["username"]] = token_data["access_token"]
        else:
            print(f"   ⚠️  {user['username']:<22} roles={user_roles} — esperaba '{user['expected_role']}'")
            all_ok = False

    print()
    return all_ok, tokens


def check_gateway(gateway: str, tokens: dict) -> bool:
    print("5️⃣  Verificando que el API Gateway valida los tokens...")

    # Sin token → debe dar 401
    r = requests.get(f"{gateway}/api/v1/slices/", timeout=5)
    if r.status_code == 401:
        print(f"   ✅ Sin token → 401 (correcto)")
    else:
        print(f"   ⚠️  Sin token → {r.status_code} (esperaba 401) — ¿JWT_ENABLED=true?")

    # Con token de usuario → debe pasar (puede dar 200, 404, 503 pero no 401)
    if "usuario.lopez" in tokens:
        token = tokens["usuario.lopez"]
        r = requests.get(
            f"{gateway}/api/v1/slices/",
            headers={"Authorization": f"Bearer {token}"},
            timeout=5,
        )
        if r.status_code not in (401, 403):
            print(f"   ✅ Con token usuario.lopez → {r.status_code} (autenticación OK)")
        else:
            print(f"   ❌ Con token usuario.lopez → {r.status_code} (la autenticación falló)")
            return False

    # Token expirado/falso → debe dar 401
    fake_token = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJmYWtlIn0.invalidsig"
    r = requests.get(
        f"{gateway}/api/v1/slices/",
        headers={"Authorization": f"Bearer {fake_token}"},
        timeout=5,
    )
    if r.status_code == 401:
        print(f"   ✅ Token falso → 401 (correcto)")
    else:
        print(f"   ⚠️  Token falso → {r.status_code} (esperaba 401)")

    print()
    return True


def print_env_vars(base: str, gateway: str):
    jwks = f"http://keycloak:8080/realms/{REALM}/protocol/openid-connect/certs"
    print("6️⃣  Variables de entorno para el API Gateway:")
    print("─" * 62)
    print(f"   JWT_ENABLED=true")
    print(f"   JWT_JWKS_URL={jwks}")
    print(f"   JWT_ISSUER={base}/realms/{REALM}")
    print(f"   JWT_AUDIENCE=   (vacío)")
    print("─" * 62)
    print(f"\n   Token de prueba (curl):")
    print(f"   curl -s -X POST '{base}/realms/{REALM}/protocol/openid-connect/token' \\")
    print(f"     -d 'grant_type=password&client_id={CLIENT_ID}' \\")
    print(f"     -d 'username=usuario.lopez&password=Usuario123!' | python3 -m json.tool")
    print()


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Verifica Keycloak + API Gateway")
    parser.add_argument("--vm", action="store_true", help="Apunta a la VM (10.20.11.212)")
    args = parser.parse_args()

    env     = ENVIRONMENTS["vm"] if args.vm else ENVIRONMENTS["local"]
    base    = env["base"]
    gateway = env["gateway"]

    print("=" * 62)
    print(f"  PUCP Cloud — Verificación de Keycloak")
    print(f"  Entorno: {env['label']}")
    print("=" * 62)
    print()

    if not check_health(base):
        sys.exit(1)

    ok = True
    ok &= check_realm(base)
    ok &= check_jwks(base)

    users_ok, tokens = check_users(base)
    ok &= users_ok

    # Verificar gateway solo si hay tokens disponibles
    try:
        gw_ok = check_gateway(gateway, tokens)
        ok &= gw_ok
    except requests.exceptions.ConnectionError:
        print(f"   ℹ️  API Gateway ({gateway}) no disponible — omitiendo paso 5\n")

    print_env_vars(base, gateway)

    if ok:
        print("✅ Todo OK — Keycloak listo para integrarse con el API Gateway.")
    else:
        print("⚠️  Hay advertencias — revisa los puntos marcados arriba.")
        sys.exit(2)


if __name__ == "__main__":
    main()
