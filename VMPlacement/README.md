# vm-placement

Microservicio HTTP de asignación de VMs a workers físicos.
Forma parte del sistema de orquestación de slices — proyecto TEL141-G8.

---

## Rol en la arquitectura

```
Slice Manager
    │
    ├─(HTTP POST /placement)──► vm-placement   ← ESTE SERVICIO
    │                                │
    │                           Round Robin
    │                                │
    ◄────────(JSON response)─────────┘
    │
    └─(continúa flujo hacia Compute Provisioner)
```

La comunicación es **HTTP síncrona request/reply**. El Slice Manager hace un POST y espera la respuesta antes de continuar. El servicio es stateless y maneja múltiples requests concurrentes sin problema (FastAPI + uvicorn async).

---

## Responsabilidad única

> Dado un conjunto de VMs con requerimientos de recursos y un conjunto de workers
> con capacidad disponible, decidir en qué worker físico se despliega cada VM.

**No hace:** instanciar VMs, gestionar redes, validar topologías, autenticar.

---

## Endpoints

### `POST /placement`

Calcula la asignación VM→Worker usando Round Robin.

**Request body:**
```json
{
  "slice_id": "abc-123",
  "availability_zone": "zona-a",
  "vms": [
    { "vm_id": "vm-1", "vcpus": 1, "ram_mb": 512, "disk_gb": 3 },
    { "vm_id": "vm-2", "vcpus": 1, "ram_mb": 512, "disk_gb": 2 },
    { "vm_id": "vm-3", "vcpus": 1, "ram_mb": 512, "disk_gb": 3 },
    { "vm_id": "vm-4", "vcpus": 1, "ram_mb": 512, "disk_gb": 2 },
    { "vm_id": "vm-5", "vcpus": 1, "ram_mb": 512, "disk_gb": 3 },
    { "vm_id": "vm-6", "vcpus": 1, "ram_mb": 512, "disk_gb": 3 }
  ],
  "workers": [
    { "worker_id": "worker-1", "available_vcpus": 10, "available_ram_mb": 16000, "available_disk_gb": 500 },
    { "worker_id": "worker-2", "available_vcpus": 10, "available_ram_mb": 16000, "available_disk_gb": 500 },
    { "worker_id": "worker-3", "available_vcpus": 10, "available_ram_mb": 16000, "available_disk_gb": 500 }
  ]
}
```

**Nota:** Los workers deben llegar ya filtrados por `availability_zone` — esa es responsabilidad del Slice Manager.

**Response — éxito (HTTP 200):**
```json
{
  "slice_id": "abc-123",
  "status": "SUCCESS",
  "placement_map": [
    { "vm_id": "vm-1", "worker_id": "worker-1" },
    { "vm_id": "vm-2", "worker_id": "worker-2" },
    { "vm_id": "vm-3", "worker_id": "worker-3" },
    { "vm_id": "vm-4", "worker_id": "worker-1" },
    { "vm_id": "vm-5", "worker_id": "worker-2" },
    { "vm_id": "vm-6", "worker_id": "worker-3" }
  ],
  "reason": null,
  "detail": null
}
```

**Response — fallo (HTTP 200):**
```json
{
  "slice_id": "abc-123",
  "status": "FAILED",
  "placement_map": null,
  "reason": "INSUFFICIENT_RESOURCES",
  "detail": "No hay worker con recursos suficientes para 'vm-3' (vcpus=1, ram_mb=512, disk_gb=3)."
}
```

| `reason` | Cuándo ocurre |
|---|---|
| `NO_WORKERS_AVAILABLE` | Lista de workers vacía |
| `INSUFFICIENT_RESOURCES` | Ningún worker tiene CPU/RAM/disco para alguna VM |

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

## Levantar con Docker Compose

```bash
docker compose up --build
```

Servicio disponible en `http://localhost:8080`.

## Levantar sin Docker (desarrollo local)

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8080
```

## Correr tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

---

## Probar con curl

```bash
curl -X POST http://localhost:8080/placement \
  -H "Content-Type: application/json" \
  -d '{
    "slice_id": "slice-001",
    "availability_zone": "zona-a",
    "vms": [
      { "vm_id": "vm-1", "vcpus": 1, "ram_mb": 512, "disk_gb": 3 },
      { "vm_id": "vm-2", "vcpus": 1, "ram_mb": 512, "disk_gb": 2 },
      { "vm_id": "vm-3", "vcpus": 1, "ram_mb": 512, "disk_gb": 3 },
      { "vm_id": "vm-4", "vcpus": 1, "ram_mb": 512, "disk_gb": 2 },
      { "vm_id": "vm-5", "vcpus": 1, "ram_mb": 512, "disk_gb": 3 },
      { "vm_id": "vm-6", "vcpus": 1, "ram_mb": 512, "disk_gb": 3 }
    ],
    "workers": [
      { "worker_id": "worker-1", "available_vcpus": 10, "available_ram_mb": 16000, "available_disk_gb": 500 },
      { "worker_id": "worker-2", "available_vcpus": 10, "available_ram_mb": 16000, "available_disk_gb": 500 },
      { "worker_id": "worker-3", "available_vcpus": 10, "available_ram_mb": 16000, "available_disk_gb": 500 }
    ]
  }'
```

---

## Estructura del código

```
vm-placement/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI app: define los endpoints HTTP
│   ├── models.py            # Modelos Pydantic de entrada y salida
│   └── placement_engine.py  # Algoritmo Round Robin puro (sin I/O, testeable)
├── tests/
│   └── test_placement.py    # Tests del engine y del endpoint HTTP
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
└── README.md
```

### Flujo interno de un request

```
POST /placement
  └─► main.py (endpoint placement)
        └─► placement_engine.py (run_placement)
              └─► retorna PlacementResponse
        └─► FastAPI serializa y responde JSON
```

---

## Notas para colaboradores

- **`placement_engine.py` es el núcleo.** Para cambiar el algoritmo (Best Fit, First Fit, etc.) solo se modifica este archivo.
- **Stateless.** Cada request es independiente. No hay base de datos ni estado en memoria entre llamadas.
- **Sin placement parcial.** Si una VM no cabe, se retorna `FAILED` sin asignar ninguna. El Slice Manager activa el rollback (patrón Saga).
- **El orden de las VMs importa.** El Slice Manager debe enviarlas en orden `vm-1, vm-2, ..., vm-n` para garantizar la distribución Round Robin esperada.
- **Los workers llegan pre-filtrados por zona.** El vm-placement no filtra por `availability_zone` — asume que todos los workers recibidos son candidatos válidos.
