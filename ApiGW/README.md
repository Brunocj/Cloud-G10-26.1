# API Gateway — PUCP Cloud Orchestrator

Punto único de entrada HTTP del sistema. Recibe todos los requests de la Web App
y los reenvía al microservicio interno correspondiente.

Implementado con **FastAPI + httpx + paramiko**: actúa como proxy transparente para
las rutas REST y como proxy WebSocket con túnel SSH para las consolas VNC.

---

## Estado actual

| Funcionalidad | Estado |
|---|---|
| Reenvío `/api/v1/slices/**` → Slice Manager | ✅ Activo |
| Validación JWT (Keycloak) + reinyección de headers `X-User-Id` / `X-User-Role` | ✅ Activo |
| CORS habilitado para Web App (localhost y VM `10.20.11.212`) | ✅ Activo |
| Proxy WebSocket VNC via túnel SSH → workers Linux Cluster | ✅ Activo |
| Proxy WebSocket VNC → nova-novncproxy de OpenStack | ✅ Activo |
| Reenvío `/auth/**` → Keycloak | 🔜 Preparado (identity.py implementado, no registrado aún) |

---

## Estructura

```
ApiGateway/
├── app/
│   ├── main.py              # FastAPI app, lifespan, CORS, JWT middleware
│   ├── config.py            # Variables de entorno (pydantic-settings)
│   ├── proxy.py             # Lógica de reenvío compartida
│   ├── middleware/
│   │   └── jwt_auth.py      # Validación RS256, extracción de rol, mutación de headers
│   └── routers/
│       ├── slices.py        # /api/v1/slices/** → Slice Manager
│       ├── vnc_proxy.py     # /vnc/**           → Consolas VNC (SSH tunnel + OpenStack)
│       └── identity.py      # /auth/**          → Keycloak (preparado, no registrado)
├── keys/
│   └── id_ed25519           # Llave SSH para túneles VNC (montada vía volumen Docker)
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Flujo de requests

```
Web App (browser)
  │
  ├── POST /auth/realms/.../token          →  Keycloak directo (no pasa por el gateway)
  │         Keycloak devuelve token JWT con realm_access.roles
  │         La Web App almacena el token
  │
  ├── * /api/v1/slices/**  + Bearer JWT    →  Slice Manager
  │         Gateway valida firma RS256 del token
  │         Gateway inyecta X-User-Id y X-User-Role, elimina Authorization
  │         Slice Manager lee esos headers sin conocer Keycloak
  │
  ├── ws://.../vnc/{gw_ip}/{ssh_port}/{ws_port}
  │         Gateway abre túnel SSH → worker Linux Cluster
  │         Hace WebSocket forward hacia QEMU (ws_port en localhost del worker)
  │
  └── ws://.../vnc/openstack/{nova_token}
            Gateway abre túnel SSH → headnode OpenStack
            Hace WebSocket forward hacia nova-novncproxy en controller:6080
```

---

## Levantar

```bash
docker compose up --build
```

Verificar:

```bash
curl http://localhost:8085/health
# {"status":"ok","service":"api-gateway","jwt_enabled":true}
```

---

## Endpoints

| Ruta | Upstream | JWT requerido |
|---|---|---|
| `GET /health` | — (propio gateway) | No |
| `* /api/v1/slices/**` | `slice-manager:8000` | Sí |
| `WS /vnc/{gw_ip}/{ssh_port}/{ws_port}` | QEMU worker (via SSH tunnel) | No (protegido por SSH) |
| `WS /vnc/openstack/{nova_token}` | nova-novncproxy controller:6080 (via SSH tunnel) | No (token de Nova) |

---

## Autenticación JWT

El middleware (`middleware/jwt_auth.py`) valida el token en cada request a `/api/v1/slices/**`:

1. Descarga las claves públicas de Keycloak desde `JWT_JWKS_URL` al arrancar.
2. Valida la firma RS256 y la expiración.
3. Extrae `sub` → `X-User-Id` y el rol de mayor prioridad de `realm_access.roles` → `X-User-Role`.
4. Elimina `Authorization` del request y añade los dos headers de identidad.
5. El Slice Manager lee esos headers sin conocer Keycloak.

**Roles reconocidos** (de mayor a menor prioridad): `superAdmin` > `admin` > `jefeProyecto` > `usuario`

Rutas públicas (sin token): `/health`, `/docs`, `/auth/**`, `/vnc/**`

---

## Proxy VNC (Linux Cluster)

URL del browser:
```
ws://apigw:8085/vnc/{gateway_ip}/{ssh_port}/{ws_port}
```

El gateway:
1. Carga la llave SSH desde `VNC_SSH_KEY_PATH` (Ed25519 / RSA / ECDSA).
2. Abre una conexión SSH al `gateway_ip:ssh_port`.
3. Crea un canal `direct-tcpip` hacia `localhost:{ws_port}` en el worker.
4. Hace WebSocket forward bidireccional entre el browser y QEMU.

Ejemplo para server1 (puerto SSH 5811) con QEMU ws_port=5744:
```
ws://apigw:8085/vnc/10.20.11.119/5811/5744
```

## Proxy VNC (OpenStack)

URL del browser:
```
ws://apigw:8085/vnc/openstack/{nova_token}
```

El gateway:
1. Abre un túnel SSH al headnode en `VNC_OS_HEADNODE_PORT`.
2. Crea un canal `direct-tcpip` hacia `VNC_OS_CONTROLLER_IP:VNC_OS_CONTROLLER_PORT`.
3. Conecta un WebSocket hacia `/websockify?token={nova_token}` y hace forward bidireccional.

---

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `SLICE_MANAGER_URL` | `http://slice-manager:8000` | URL interna del Slice Manager |
| `FORWARD_TIMEOUT` | `30.0` | Timeout en segundos para reenvío HTTP |
| `LOG_LEVEL` | `INFO` | Nivel de logging |
| `JWT_ENABLED` | `false` | Activa validación JWT |
| `JWT_JWKS_URL` | `http://keycloak:8080/realms/pucp-cloud/protocol/openid-connect/certs` | URL del JWKS de Keycloak |
| `JWT_ISSUER` | `http://10.20.11.212:8086/realms/pucp-cloud` | Issuer esperado en el token (debe coincidir con `iss`) |
| `JWT_AUDIENCE` | `""` | Audience esperado (vacío = no verificar `aud`) |
| `VNC_SSH_KEY_PATH` | `/app/keys/id_ed25519` | Ruta a la llave privada SSH para túneles VNC |
| `VNC_SSH_USER` | `ubuntu` | Usuario SSH para los túneles VNC |
| `VNC_OS_HEADNODE_PORT` | `5821` | Puerto SSH del headnode OpenStack |
| `VNC_OS_CONTROLLER_IP` | `192.168.202.1` | IP del controller OpenStack (nova-novncproxy) |
| `VNC_OS_CONTROLLER_PORT` | `6080` | Puerto del nova-novncproxy |

> `JWT_ISSUER` debe usar la URL pública de Keycloak (la que aparece en el campo `iss` del token),
> no el nombre de servicio Docker interno.

---

## Activar el router de Keycloak (`/auth/**`)

`identity.py` está implementado como proxy hacia Keycloak, pero no está registrado en `main.py`.
Cuando se necesite:

**1. `config.py`** — agregar la variable:

```python
KEYCLOAK_URL: str = "http://keycloak:8080"
```

**2. `main.py`** — registrar el router:

```python
from app.routers import identity
app.include_router(identity.router)
```

**3. `docker-compose.yml`** — agregar la variable de entorno:

```yaml
KEYCLOAK_URL: http://keycloak:8080
```
