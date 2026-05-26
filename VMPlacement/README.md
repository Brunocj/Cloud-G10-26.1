# VM Placement — PUCP Cloud Orchestrator

Microservicio HTTP de asignación óptima de VMs a workers físicos.
Recibe las VMs a desplegar (con su peso pre-calculado) y el Servers' State
(capacidad disponible por worker en la zona de disponibilidad), y devuelve
el mapa `vm_id → worker_id` usando el algoritmo LLF-D.

---

## Rol en la arquitectura

```
Slice Manager
    │
    ├─(HTTP POST /placement)──► VM Placement   ← ESTE SERVICIO
    │                                │
    │                         LLF-D + Max Heap
    │                                │
    ◄────────(JSON response)─────────┘
    │
    └─(continúa: enriquecimiento → NATS → Queue Manager)
```

La comunicación es **HTTP síncrona request/reply**. El Slice Manager construye
el Servers' State directamente desde la BD (workers de la zona + pesos de VMs
activas con overprovisioning), lo empaqueta junto a las VMs del slice y hace un POST.
Este servicio responde con el mapa completo antes de que el Slice Manager continúe.

---

## Algoritmo: LLF-D

**Least Loaded First con ordenamiento Decreciente.**

### Función objetivo

```
min( max( carga_i / C_i ) )   ∀ i ∈ workers de la zona
```

Minimiza la utilización máxima entre los workers de la zona, lo que equivale
a maximizar el balance de carga. `carga_i = C_i - D_i`, donde `D_i` es la
capacidad disponible del worker i en unidades de peso.

### Criterio de selección por iteración

```
worker* = argmax_i( D_i )
```

En cada paso se elige el worker con mayor capacidad disponible. Implementado
con un **Max Heap** para acceso O(1) y actualización O(log m).

### Pasos

1. Ordenar VMs del slice de mayor a menor peso — **O(n log n)**
2. Para cada VM (en ese orden):
   - Tomar el worker con mayor `D_i` del heap — **O(1)**
   - Si `D_i < peso_vm` → retornar `FAILED` (sin recursos suficientes)
   - Asignar VM al worker y descontar peso del heap — **O(log m)**
3. Retornar mapa completo.

### Complejidad total

```
O(n log n + n log m)
```

donde `n` = VMs del slice y `m` = workers disponibles en la zona.

### Timeout dinámico

```
timeout = n × 1 segundo
```

El tiempo máximo de ejecución escala con el tamaño del slice.

---

## Modelo de pesos

El peso de cada VM es calculado por el **Slice Manager al persistir la VM en BD**
usando la fórmula:

```
w = 3·vcpus + 5·ram_gb + 1·disco_gb
```

Con coeficientes derivados del cuello de botella del clúster (RAM es el recurso más escaso):

```
β (RAM) : α (vCPU) : γ (disco) = 5 : 3 : 1
```

Este servicio **recibe el peso ya calculado** — no lo recomputa.

El Slice Manager envía `peso_actualizado` de cada VM (que el módulo de Observabilidad
irá corrigiendo con consumo real). Al arrancar el sistema, `peso_actualizado == peso`.

---

## Endpoints

### `POST /placement`

**Request body:**
```json
{
  "slice_id": "42",
  "availability_zone": "1",
  "vms": [
    { "vm_id": "n214", "peso": 24.0 },
    { "vm_id": "n215", "peso": 18.0 }
  ],
  "workers": [
    { "worker_id": 2, "disponible": 36.92 },
    { "worker_id": 3, "disponible": 36.92 },
    { "worker_id": 4, "disponible": 36.92 }
  ]
}
```

> `availability_zone` es el ID de la zona (string) — no el nombre.  
> `disponible` es la capacidad restante del worker en unidades de peso,
> calculada por el Slice Manager como `C_i − Σ peso_actualizado(VMs ACTIVE)`.  
> Workers con `id=1` (headnode) nunca aparecen — el Slice Manager los excluye.

**Response — éxito (HTTP 200):**
```json
{
  "slice_id": "42",
  "status": "SUCCESS",
  "placement_map": [
    { "vm_id": "n214", "worker_id": 3 },
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
  "reason": "INSUFFICIENT_RESOURCES",
  "detail": "No hay worker con capacidad suficiente para VM 'n214' (peso=999.0, máx disponible=36.92)."
}
```

| `reason` | Cuándo ocurre |
|---|---|
| `NO_WORKERS_AVAILABLE` | Lista de workers vacía |
| `INSUFFICIENT_RESOURCES` | Ningún worker tiene capacidad para alguna VM del slice |
| `TIMEOUT` | El placement no concluyó en `n × 1s` |

> El slice es atómico: si una VM no puede asignarse, se retorna `FAILED` para
> todo el slice. El Slice Manager activa rollback (patrón Saga).

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
│   ├── __init__.py
│   ├── main.py              # FastAPI app: endpoints, timeout dinámico, logging
│   ├── models.py            # Pydantic: PlacementRequest, PlacementResponse, VMSpec, WorkerState
│   └── placement_engine.py  # LLF-D con Max Heap
├── docker-compose.yml
├── Dockerfile
├── README.md
└── requirements.txt
```

### Modelos Pydantic

```python
class VMSpec(BaseModel):
    vm_id: str
    peso: float          # Pre-calculado por el Slice Manager al persistir la VM en BD

class WorkerState(BaseModel):
    worker_id: int
    disponible: float    # C_i - Σ peso_actualizado(VMs ACTIVE) — calculado por el Slice Manager

class PlacementRequest(BaseModel):
    slice_id: str
    availability_zone: str   # ID de la zona como string
    vms: List[VMSpec]
    workers: List[WorkerState]
```

### Flujo interno de un request

```
POST /placement
  └─► main.py (endpoint /placement)
        └─► calcula timeout = n × 1s
        └─► asyncio.wait_for(run_in_executor(run_placement), timeout)
              └─► placement_engine.run_placement(vms, workers)
                    └─► sort VMs por peso desc       O(n log n)
                    └─► heapify workers              O(m)
                    └─► loop VMs → heapreplace       O(n log m)
                    └─► retorna (success, map, reason, detail)
        └─► PlacementResponse serializada como JSON
```

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
    "availability_zone": "1",
    "vms": [
      { "vm_id": "n214", "peso": 18.5 },
      { "vm_id": "n215", "peso": 5.25 }
    ],
    "workers": [
      { "worker_id": 2, "disponible": 36.92 },
      { "worker_id": 3, "disponible": 36.92 },
      { "worker_id": 4, "disponible": 36.92 }
    ]
  }'
```

---

## Notas para colaboradores

- **El peso llega pre-calculado.** El Slice Manager aplica `w = 3·vcpus + 5·ram_gb + 1·disk_gb`
  al persistir la VM en BD. Este servicio no conoce los coeficientes ni los recursos crudos.
- **El Servers' State lo construye el Slice Manager.** Consulta la BD por workers de la zona,
  aplica `F_OP = 1/0.65 ≈ 1.54` sobre la capacidad nominal y suma los `peso_actualizado`
  de VMs activas. Este servicio recibe el estado ya calculado en campo `disponible`.
- **`availability_zone` es el ID como string.** El Slice Manager envía el `id` de la zona,
  no el nombre (`"1"`, no `"Linux Cluster"`).
- **Sin placement parcial.** Si una VM no puede asignarse, se retorna `FAILED` sin
  asignar ninguna. El Slice Manager activa rollback (patrón Saga).
- **`worker_id` es siempre `int`.** El Slice Manager lo espera así para hacer lookup
  en su `server_inventory`.
