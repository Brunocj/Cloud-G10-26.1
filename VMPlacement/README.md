# VM Placement — PUCP Cloud Orchestrator

Microservicio HTTP de asignación de VMs a workers físicos.
Recibe una lista de VMs con requerimientos de recursos y una lista de workers
con capacidad disponible, y devuelve el mapa `vm_id → worker_id`.

---

## Rol en la arquitectura

```
Slice Manager
    │
    ├─(HTTP POST /placement)──► VM Placement   ← ESTE SERVICIO
    │                                │
    │                      Round Robin (ver estado actual)
    │                                │
    ◄────────(JSON response)─────────┘
    │
    └─(continúa: enriquecimiento → NATS → Queue Manager)
```

La comunicación es **HTTP síncrona request/reply**. El Slice Manager hace un POST
y espera la respuesta antes de continuar con el enriquecimiento del contrato.

---

## Estado actual del algoritmo

El servicio tiene dos engines implementados:

| Archivo | Algoritmo | Estado |
|---------|-----------|--------|
| `placement_engine_temp.py` | **Round Robin puro** — asigna circularmente sin verificar recursos | ✅ **Activo** |
| `placement_engine.py` | Round Robin con verificación de CPU/RAM/disco | 🔜 Preparado, pendiente de activar |

El engine activo es `placement_engine_temp.py`. Ambos mantienen el índice Round Robin
en una variable global en memoria (`LAST_WORKER_INDEX` / `GLOBAL_RR_INDEX`), lo que
hace que el estado **persista entre distintas peticiones** dentro de la misma ejecución
del contenedor.

Para activar el engine con verificación de recursos, cambiar en `main.py`:
```python
# Antes (activo):
from app.placement_engine_temp import run_placement_temp
return run_placement_temp(request)

# Después:
from app.placement_engine import run_placement
return run_placement(request)
```

---

## Endpoints

### `POST /placement`

Calcula la asignación VM→Worker y devuelve el mapa completo.

**Request body:**
```json
{
  "slice_id": "42",
  "availability_zone": "Linux Cluster",
  "vms": [
    { "vm_id": "n214", "vcpus": 1, "ram_mb": 512.0, "disk_gb": 10.0 },
    { "vm_id": "n215", "vcpus": 2, "ram_mb": 1024.0, "disk_gb": 20.0 }
  ],
  "workers": [
    { "worker_id": 1, "available_vcpus": 10, "available_ram_mb": 16000.0, "available_disk_gb": 500.0 },
    { "worker_id": 2, "available_vcpus": 10, "available_ram_mb": 16000.0, "available_disk_gb": 500.0 },
    { "worker_id": 3, "available_vcpus": 10, "available_ram_mb": 16000.0, "available_disk_gb": 500.0 },
    { "worker_id": 4, "available_vcpus": 10, "available_ram_mb": 16000.0, "available_disk_gb": 500.0 }
  ]
}
```

> `worker_id` es un entero (`int`), no un string. El Slice Manager envía
> los workers con IDs enteros (1, 2, 3, 4) y espera recibirlos de la misma forma.

**Response — éxito (HTTP 200):**
```json
{
  "slice_id": "42",
  "status": "SUCCESS",
  "placement_map": [
    { "vm_id": "n214", "worker_id": 1 },
    { "vm_id": "n215", "worker_id": 2 }
  ],
  "reason": null,
  "detail": null
}
```

**Response — fallo (HTTP 200):**
```json
{
  "slice_id": "42",
  "status": "FAILED",
  "placement_map": null,
  "reason": "NO_WORKERS_AVAILABLE",
  "detail": "La lista de workers está vacía."
}
```

| `reason` | Cuándo ocurre |
|---|---|
| `NO_WORKERS_AVAILABLE` | Lista de workers vacía |
| `INSUFFICIENT_RESOURCES` | Ningún worker tiene CPU/RAM/disco para alguna VM (solo con engine completo) |

> Con el engine temporal (`placement_engine_temp.py`), nunca se devuelve
> `INSUFFICIENT_RESOURCES` — el Round Robin puro no verifica recursos.

---

### `GET /health`

Liveness check.

```json
{ "status": "ok" }
```

---

### Docs interactiva (Swagger)

Con el servicio corriendo: `http://localhost:8080/docs`

---

## Estructura del código

```
vm-placement/
├── app/
│   ├── main.py                    # FastAPI app: endpoints HTTP
│   ├── models.py                  # Pydantic: PlacementRequest, PlacementResponse
│   ├── placement_engine_temp.py   # Round Robin puro (activo)
│   └── placement_engine.py        # Round Robin con verificación de recursos (pendiente)
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

### Flujo interno de un request

```
POST /placement
  └─► main.py (endpoint placement)
        └─► placement_engine_temp.run_placement_temp(request)
              └─► Round Robin puro sobre workers
              └─► retorna PlacementResponse
        └─► FastAPI serializa y responde JSON
```

---

## Comportamiento del Round Robin

El índice global avanza con cada VM asignada y **persiste entre requests**.
Esto garantiza distribución equitativa a lo largo del tiempo, no solo dentro de un slice.

Ejemplo con 3 workers y dos requests consecutivos de 3 VMs cada uno:

```
Request 1 (slice-001): n214→worker1, n215→worker2, n216→worker3
Request 2 (slice-002): n217→worker1, n218→worker2, n219→worker3
                             ↑ el índice continuó desde donde quedó
```

> Si el contenedor se reinicia, el índice vuelve a 0. El comportamiento
> entre reinicios no está garantizado — es un estado en memoria.

---

## Diferencia entre los dos engines

| Característica | `placement_engine_temp` | `placement_engine` |
|---|---|---|
| Algoritmo | Round Robin puro | Round Robin con recursos |
| Verifica CPU/RAM/disco | ❌ No | ✅ Sí |
| Puede retornar FAILED por recursos | ❌ No | ✅ Sí |
| Descuenta recursos al asignar | ❌ No | ✅ Sí (localmente, no persiste) |
| Estado entre requests | `LAST_WORKER_INDEX` global | `GLOBAL_RR_INDEX` global |

---

## Despliegue con Docker

```bash
docker compose up --build
```

Servicio disponible en `http://localhost:8080`.

---

## Desarrollo local (sin Docker)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```

---

## Probar con curl

```bash
curl -X POST http://localhost:8080/placement \
  -H "Content-Type: application/json" \
  -d '{
    "slice_id": "42",
    "availability_zone": "Linux Cluster",
    "vms": [
      { "vm_id": "n214", "vcpus": 1, "ram_mb": 512.0, "disk_gb": 10.0 },
      { "vm_id": "n215", "vcpus": 1, "ram_mb": 512.0, "disk_gb": 10.0 },
      { "vm_id": "n216", "vcpus": 2, "ram_mb": 1024.0, "disk_gb": 20.0 }
    ],
    "workers": [
      { "worker_id": 1, "available_vcpus": 10, "available_ram_mb": 16000.0, "available_disk_gb": 500.0 },
      { "worker_id": 2, "available_vcpus": 10, "available_ram_mb": 16000.0, "available_disk_gb": 500.0 },
      { "worker_id": 3, "available_vcpus": 10, "available_ram_mb": 16000.0, "available_disk_gb": 500.0 }
    ]
  }'
```

---

## Notas para colaboradores

- **Los workers llegan pre-filtrados por zona.** El VM Placement no filtra por
  `availability_zone` — asume que todos los workers recibidos son candidatos válidos.
  Es responsabilidad del Slice Manager filtrarlos antes de llamar a este servicio.
- **El `worker_id` es siempre `int`.** Los modelos Pydantic lo declaran como `int`
  y el Slice Manager lo espera así para hacer lookup en su `server_inventory`.
- **Sin placement parcial.** Si una VM no puede ser asignada (con el engine completo),
  se retorna `FAILED` sin asignar ninguna. El Slice Manager activa rollback (patrón Saga).
- **El orden de las VMs importa.** La distribución Round Robin depende del orden
  en que el Slice Manager envía las VMs.
