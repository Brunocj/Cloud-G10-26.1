# API Gateway — PUCP Cloud Orchestrator

Punto único de entrada HTTP del sistema. Recibe todos los requests de la Web App
y los reenvía al microservicio interno correspondiente.

Implementado con **FastAPI + httpx**: el gateway actúa como proxy transparente,
conservando método, headers, query string y body exactamente como llegan.

---

## Estado actual

| Funcionalidad | Estado |
|---|---|
| Reenvío `/api/v1/slices/**` → Slice Manager | ✅ Activo |
| Reenvío `/auth/**` → Keycloak | 🔜 Preparado (503 hasta que Keycloak exista) |
| CORS habilitado para Web App (`localhost:5173`) | ✅ Activo |
| Validación JWT (Keycloak) + reinyección de claims | 🔜 Preparado, pendiente de activar |

---

## Estructura

```
ApiGateway/
├── app/
│   ├── main.py              # FastAPI app, lifespan, CORS
│   ├── config.py            # Variables de entorno (pydantic-settings)
│   ├── proxy.py             # Lógica de reenvío compartida
│   └── routers/
│       ├── slices.py        # /api/v1/slices/** → Slice Manager
│       └── identity.py      # /auth/**          → Keycloak
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

---

## Flujo de requests

```
Web App (localhost:5173)
  │
  ├── POST /auth/**                →  Keycloak  (sin JWT, ruta pública)
  │         Keycloak responde con token JWT
  │         La Web App lo almacena en el cliente
  │
  └── * /api/v1/slices/**          →  Slice Manager
            (futuro) Gateway valida JWT antes de reenviar
            (futuro) Gateway inyecta X-User-Id y X-User-Role como headers internos
```

---

## Levantar

```bash
docker compose up --build
```

Verificar:

```bash
curl http://localhost:8085/health
# {"status":"ok","service":"api-gateway"}
```

---

## Endpoints

| Ruta | Upstream | JWT requerido |
|---|---|---|
| `GET /health` | — (propio gateway) | No |
| `* /api/v1/slices/**` | `slice-manager:8000` | No (futuro: Sí) |
| `* /auth/**` | `keycloak:8080` | No (ruta pública) |

---

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `SLICE_MANAGER_URL` | `http://slice-manager:8000` | URL interna del Slice Manager |
| `FORWARD_TIMEOUT` | `30.0` | Timeout en segundos para reenvío |
| `LOG_LEVEL` | `INFO` | Nivel de logging |
| `JWT_ENABLED` | `false` | Activa validación JWT |
| `JWT_JWKS_URL` | `""` | URL del JWKS de Keycloak |
| `JWT_ISSUER` | `""` | Issuer esperado en el token |
| `JWT_AUDIENCE` | `""` | Audience esperado en el token |

> `KEYCLOAK_URL` no está activo todavía — `identity.py` está implementado pero no registrado en los routers.

---

## Probar el reenvío al Slice Manager

Con el gateway y el Slice Manager corriendo en la misma red Docker (`pucp_cloud_net`),
cualquier request a `/api/v1/slices` llega al gateway en el puerto `8085` y es
reenviado transparentemente al Slice Manager. El JSON que mandas es exactamente
el que recibe el Slice Manager, sin modificaciones.

```bash
# Crear un slice
curl -X POST http://localhost:8085/api/v1/slices \
  -H "Content-Type: application/json" \
  -d '{
    "name": "mi-slice",
    "vms": [
      {"vm_id": "vm-1", "vcpus": 1, "ram_mb": 512, "disk_gb": 5}
    ]
  }'

# Listar slices
curl http://localhost:8085/api/v1/slices

# Consultar un slice específico
curl http://localhost:8085/api/v1/slices/slice-001

# Eliminar un slice
curl -X DELETE http://localhost:8085/api/v1/slices/slice-001
```

Lo que ocurre internamente en cada request:

```
curl → POST http://localhost:8085/api/v1/slices
             ↓
         API Gateway (puerto 8085)
         recibe el request, conserva método + headers + body
             ↓
         reenvía a POST http://slice-manager:8000/api/v1/slices
             ↓
         Slice Manager procesa y responde
             ↓
         API Gateway devuelve la respuesta al cliente sin tocarla
```

El gateway es completamente transparente: no inspecciona el body, no lo modifica,
no agrega lógica de negocio. Solo cambia el destino del request.

---

## Activar JWT (cuando Keycloak esté listo)

**1. `docker-compose.yml`** — completar las variables:

```yaml
JWT_ENABLED:  "true"
JWT_JWKS_URL: "http://keycloak:8080/auth/realms/pucp-cloud/protocol/openid-connect/certs"
JWT_ISSUER:   "http://keycloak:8080/auth/realms/pucp-cloud"
JWT_AUDIENCE: "account"
```

**2. `requirements.txt`** — descomentar:

```
PyJWT==2.8.0
cryptography==42.0.8
```

**3. `app/routers/slices.py`** — en `_build_forward_headers`, descomentar el bloque
marcado con `# Futuro` para inyectar `X-User-Id` y `X-User-Role` extraídos del token.

Con esos tres cambios el gateway valida la firma RS256 del token en cada request
a `/api/v1/slices/**` e inyecta la identidad hacia el Slice Manager. El Slice Manager
lee esos headers sin necesidad de conocer Keycloak.

---

## Activar el router de Keycloak (`/auth/**`)

`identity.py` ya está implementado como proxy hacia Keycloak, pero aún no está
registrado en `main.py`. Cuando Keycloak esté listo:

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
