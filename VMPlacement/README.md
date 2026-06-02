# VM Placement — PUCP Cloud Orchestrator

Microservicio HTTP de asignación óptima de VMs a workers físicos.
Recibe las VMs a desplegar (vcpus, ram_gb, disco_gb) y el Servers' State
(capacidad efectiva disponible por worker y dimensión, con overcommit estadístico
ya aplicado por el Slice Manager), y devuelve el mapa `vm_id → worker_id`
usando el solver **CP-SAT de Google OR-Tools**.

---

## Rol en la arquitectura

```
Slice Manager
    │
    ├─(HTTP POST /placement)──► VM Placement   ← ESTE SERVICIO
    │                                │
    │                         CP-SAT (OR-Tools)
    │                         knapsack 3D + makespan
    │                                │
    ◄────────(JSON response)─────────┘
    │
    └─(continúa: enriquecimiento → NATS → Queue Manager)
```

La comunicación es **HTTP síncrona request/reply**. El Slice Manager construye
el Servers' State desde la BD (workers de la zona, capacidades nominales × OC_r[j]
calculados por Observabilidad, menos recursos de VMs activas), lo empaqueta junto
a las VMs del slice y hace un POST. Este servicio responde con el mapa completo
antes de que el Slice Manager continúe.

---

## Algoritmo: CP-SAT multidimensional

El problema se modela como un **knapsack determinístico multidimensional**:
los recursos solicitados de cada VM son enteros fijos (hard limit) y la capacidad
efectiva de cada worker por dimensión incorpora el overcommit estadístico calculado
por Observabilidad (`C_efectivo_r[j] = C_nominal_r[j] × OC_r[j]`).

### Formulación

**Variables de decisión:**
```
x[i][j] ∈ {0, 1}   →   1 si VM i se asigna al worker j
```

**Restricción de asignación única:**
```
Σ_j x[i][j] = 1   ∀ i
```

**Restricciones de capacidad (tres independientes):**
```
Σ_i vcpus(i)    · x[i][j] ≤ disponible_cpu[j]    ∀ j
Σ_i ram_gb(i)   · x[i][j] ≤ disponible_ram[j]    ∀ j
Σ_i disco_gb(i) · x[i][j] ≤ disponible_disco[j]  ∀ j
```

**Función objetivo — makespan ponderado:**
```
min( max_j( Σ_r α_r · Σ_i recurso_r(i) · x[i][j] / C_efectivo_r[j] ) )
```

Coeficientes de importancia relativa por dimensión:

| Dimensión | Coeficiente |
|-----------|-------------|
| RAM       | β = 5       |
| vCPU      | α = 3       |
| Disco     | γ = 1       |

Dado que `OC_r[j]` varía por worker, el makespan refleja la heterogeneidad real
del clúster: workers con VMs de alto consumo tienen menor `C_efectivo_r[j]` y
por tanto mayor presión en el objetivo.

### Timeout dinámico

```
timeout = n segundos   (mínimo 1s)
```

donde `n` = número de VMs del slice. El límite se pasa **directamente al solver**
vía `parameters.max_time_in_seconds`. Si el solver no encuentra solución factible
en ese tiempo, retorna `FAILED` con reason `TIMEOUT`.

### Escala interna

CP-SAT opera sobre enteros. Los floats se escalan por `SCALE = 1000` antes de
construir el modelo y se revierten al interpretar el resultado.

---

## Overcommit estadístico (responsabilidad del Slice Manager)

Este servicio **recibe las capacidades ya ajustadas** — no aplica OC_r.
El Slice Manager calcula `disponible_r[j]` antes de llamar a este endpoint:

```
disponible_r[j] = C_nominal_r[j] × OC_r[j]  −  Σ_activas recurso_r(vm)
```

donde `OC_r[j]` es calculado periódicamente por Observabilidad:

```
OC_r[j] = C_nominal_r[j] / ( μ_consumo_r[j] + z_r · σ_r[j] )
```

Con factores de seguridad diferenciados:
- `z_cpu = 2`  → cobertura ~95.4 % (overcommit agresivo, throttling recuperable)
- `z_ram = 5`  → cobertura ~99.9999 % (overcommit cuidadoso, OOM-killer irreversible)
- `OC_disco = 1` → sin overcommit (asignación persistente)
- Techo duro: `OC_ram[j] ≤ 1.6`

---

## Endpoints

### `POST /placement`

**Request body:**
```json
{
  "slice_id": "42",
  "availability_zone": "1",
  "vms": [
    { "vm_id": "n214", "vcpus": 2.0, "ram_gb": 4.0, "disco_gb": 20.0 },
    { "vm_id": "n215", "vcpus": 1.0, "ram_gb": 2.0, "disco_gb": 10.0 }
  ],
  "workers": [
    { "worker_id": 2, "disponible_cpu": 12.5, "disponible_ram": 28.3, "disponible_disco": 180.0 },
    { "worker_id": 3, "disponible_cpu": 10.0, "disponible_ram": 24.0, "disponible_disco": 200.0 },
    { "worker_id": 4, "disponible_cpu": 14.0, "disponible_ram": 30.0, "disponible_disco": 150.0 }
  ]
}
```

> `disponible_r[j]` es la capacidad efectiva restante por dimensión, calculada
> por el Slice Manager como `C_efectivo_r[j] − Σ recurso_r(VMs ACTIVE)`.
> Workers con `id=1` (headnode) nunca aparecen — el Slice Manager los excluye.

**Response — éxito (HTTP 200):**
```json
{
  "slice_id": "42",
  "status": "SUCCESS",
  "placement_map": [
    { "vm_id": "n214", "worker_id": 4 },
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
  "detail": "No existe asignación factible: la zona no dispone de capacidad suficiente para todas las VMs del slice."
}
```

| `reason` | Cuándo ocurre |
|---|---|
| `NO_WORKERS_AVAILABLE` | Lista de workers vacía |
| `INSUFFICIENT_RESOURCES` | No existe asignación factible (solver INFEASIBLE) |
| `TIMEOUT` | El solver no encontró solución factible en `n × 1s` |
| `INTERNAL_ERROR` | Error inesperado en el motor de placement |

> El slice es atómico: si una VM no puede asignarse, se retorna `FAILED` para
> todo el slice. El Slice Manager retorna error al usuario (no hay rollback porque
> el placement es el primer paso de la secuencia de despliegue).

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
│   ├── main.py              # FastAPI app: endpoints, timeout, logging
│   ├── models.py            # Pydantic: PlacementRequest, PlacementResponse, VMSpec, WorkerState
│   └── placement_engine.py  # CP-SAT: knapsack 3D + makespan ponderado
├── docker-compose.yml
├── Dockerfile
├── README.md
└── requirements.txt
```

### Modelos Pydantic

```python
class VMSpec(BaseModel):
    vm_id: str
    vcpus: float      # vCPUs solicitadas
    ram_gb: float     # RAM solicitada en GB
    disco_gb: float   # Disco solicitado en GB

class WorkerState(BaseModel):
    worker_id: int
    disponible_cpu: float    # C_efectivo_cpu[j] - Σ vcpus(VMs ACTIVE)
    disponible_ram: float    # C_efectivo_ram[j] - Σ ram_gb(VMs ACTIVE)
    disponible_disco: float  # C_efectivo_disco[j] - Σ disco_gb(VMs ACTIVE)

class PlacementRequest(BaseModel):
    slice_id: str
    availability_zone: str
    vms: List[VMSpec]
    workers: List[WorkerState]
```

### Flujo interno de un request

```
POST /placement
  └─► main.py (endpoint /placement)
        └─► calcula timeout = max(n, 1) segundos
        └─► run_in_executor(run_placement, vms, workers, timeout)
              └─► placement_engine.run_placement(...)
                    └─► validaciones previas (workers vacíos, recursos excedidos)
                    └─► construye modelo CP-SAT
                          └─► variables x[i][j]
                          └─► restricción asignación única
                          └─► 3 restricciones de capacidad (cpu, ram, disco)
                          └─► makespan ponderado (α=3, β=5, γ=1)
                    └─► solver.Solve(model) con time_limit = timeout
                    └─► interpreta OPTIMAL / FEASIBLE / INFEASIBLE / UNKNOWN
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
      { "vm_id": "n214", "vcpus": 2.0, "ram_gb": 4.0, "disco_gb": 20.0 },
      { "vm_id": "n215", "vcpus": 1.0, "ram_gb": 2.0, "disco_gb": 10.0 }
    ],
    "workers": [
      { "worker_id": 2, "disponible_cpu": 12.5, "disponible_ram": 28.3, "disponible_disco": 180.0 },
      { "worker_id": 3, "disponible_cpu": 10.0, "disponible_ram": 24.0, "disponible_disco": 200.0 },
      { "worker_id": 4, "disponible_cpu": 14.0, "disponible_ram": 30.0, "disponible_disco": 150.0 }
    ]
  }'
```

---

## Notas para colaboradores

- **Los recursos llegan sin agregar.** El Slice Manager persiste `{vcpus, ram_gb, disco_gb}`
  en BD y los envía directamente. Este servicio no calcula ningún peso escalar.
- **El Servers' State lo construye el Slice Manager.** Lee `OC_r[j]` de BD
  (calculados por Observabilidad), aplica `C_efectivo_r[j] = C_nominal_r[j] × OC_r[j]`
  y resta el consumo de VMs activas. Este servicio recibe `disponible_r[j]` ya calculado.
- **`availability_zone` es el ID como string.** El Slice Manager envía el `id` de la zona,
  no el nombre (`"1"`, no `"Linux Cluster"`).
- **Sin placement parcial.** Si el solver retorna INFEASIBLE o UNKNOWN, se retorna
  `FAILED` para el slice completo. No hay asignaciones parciales.
- **`worker_id` es siempre `int`.** El Slice Manager lo espera así para hacer lookup
  en su `server_inventory`.
- **El timeout va al solver, no a asyncio.** `parameters.max_time_in_seconds` controla
  el límite de tiempo directamente en CP-SAT. No se usa `asyncio.wait_for`.
