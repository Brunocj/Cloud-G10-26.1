# API Gateway — PUCP Cloud Orchestrator

Punto único de entrada HTTP del sistema. Recibe todos los requests de la Web App
y los reenvía al microservicio interno correspondiente.

Implementado con **Starlette** (no FastAPI): el gateway no define un API propio,
no valida bodies ni serializa JSON — solo reenvía bytes. Starlette es suficiente.

---

## Estado actual

| Funcionalidad | Estado |
|---|---|
| Reenvío `/api/v1/slices/**` → Slice Manager | ✅ Activo |
| Reenvío `/auth/**` → Keycloak | ✅ Activo (503 hasta que Keycloak exista) |
| Validación JWT (Keycloak) + reinyección de claims | 🔜 Preparado, pendiente de activar |

---

## Estructura

```
ApiGateway/
├── app/
│   ├── main.py              # Starlette app, rutas, lifespan
│   ├── config.py            # Variables de entorno
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
Web App
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
curl http://localhost:8082/health
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
| `KEYCLOAK_URL` | `http://keycloak:8080` | URL interna de Keycloak |
| `FORWARD_TIMEOUT` | `30` | Timeout en segundos para reenvío |
| `LOG_LEVEL` | `INFO` | Nivel de logging |
| `JWT_ENABLED` | `false` | Activa validación JWT |
| `JWT_JWKS_URL` | `""` | URL del JWKS de Keycloak |
| `JWT_ISSUER` | `""` | Issuer esperado en el token |
| `JWT_AUDIENCE` | `""` | Audience esperado en el token |

---

## Probar el reenvío al Slice Manager

Con el gateway y el Slice Manager corriendo, cualquier request a `/api/v1/slices`
llega al gateway en el puerto `8082` y es reenviado transparentemente al Slice Manager.
El JSON que mandas es exactamente el que recibe el Slice Manager, sin modificaciones.

```bash
# Crear un slice
curl -X POST http://localhost:8082/api/v1/slices \
  -H "Content-Type: application/json" \
  -d '{
    "name": "mi-slice",
    "vms": [
      {"vm_id": "vm-1", "vcpus": 1, "ram_mb": 512, "disk_gb": 5}
    ]
  }'

# Listar slices
curl http://localhost:8082/api/v1/slices

# Consultar un slice específico
curl http://localhost:8082/api/v1/slices/slice-001

# Eliminar un slice
curl -X DELETE http://localhost:8082/api/v1/slices/slice-001
```

Lo que ocurre internamente en cada request:

```
curl → POST http://localhost:8082/api/v1/slices
             ↓
         API Gateway (puerto 8082)
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

**3. `app/routers/slices.py`** — descomentar los dos bloques marcados con `# ── JWT`.

Con esos tres cambios el gateway valida la firma RS256 del token en cada request
a `/api/v1/slices/**` e inyecta `X-User-Id` y `X-User-Role` hacia el Slice Manager.
El Slice Manager lee esos headers sin necesidad de conocer Keycloak.
