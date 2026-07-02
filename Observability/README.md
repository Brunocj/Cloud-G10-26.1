# Observability — PUCP Cloud Orchestrator

Microservicio independiente que implementa el feedback loop de VM Placement.
Consulta Prometheus periódicamente, calcula estadísticas de uso por worker usando
una ventana deslizante de 15 días con el algoritmo de Welford incremental, y
escribe los factores de overcommit (OC) en MySQL para que el Slice Manager los
use en el siguiente placement.

---

## Responsabilidades

- Consultar métricas instantáneas de CPU y RAM del host por worker desde Prometheus (vía `node_exporter`).
- Mantener un baseline por worker: consumo del host cuando no hay VMs activas.
- Calcular el ratio de eficiencia de CPU: `ratio_cpu = (consumo_real - baseline) / nominal_solicitado`.
- Mantener una ventana deslizante de 15 días de ratios de CPU por worker con Welford incremental — O(1) por ciclo.
- Calcular el factor OC de CPU por worker: `OC_cpu[j] = 1 / (μ_ratio_cpu[j] + K_CPU · σ_ratio_cpu[j])`.
- Escribir `oc_cpu`, `oc_ram`, `oc_disco` en la tabla `workers` de MySQL. **`oc_ram` es un valor fijo (2.0)** — no se calcula estadísticamente.
- Exponer endpoints REST para monitoreo de métricas y estado de la ventana.
- Exponer dashboard web opcional (controlado por `ENABLE_DASHBOARD`).

**No** toma decisiones de placement.
**No** mueve ni migra VMs.
**No** modifica el Slice Manager ni el VM Placement directamente.

---

## Arquitectura

```
observability/
├── app/
│   ├── main.py                      → FastAPI app + lifespan (arranca scheduler)
│   ├── config.py                    → Variables de entorno
│   ├── database.py                  → ORM SQLAlchemy (Worker, Vm, Slice)
│   ├── targets.py                   → Carga y cachea targets.yml (libvirt + node)
│   └── services/
│       ├── prometheus_client.py     → Queries PromQL instantáneas (httpx)
│       ├── weight_updater.py        → Baseline + ratio + Welford + cálculo OC + escritura BD
│       └── scheduler.py             → Background loop periódico
├── routers/
│   ├── metrics_router.py            → GET /metrics/workers, /metrics/workers/{id},
│   │                                   /metrics/workers/{id}/window, /metrics/status
│   └── dashboard_router.py          → GET /dashboard (solo si ENABLE_DASHBOARD=true)
├── static/
│   └── dashboard.html               → Dashboard de monitoreo (auto-refresh 1s)
├── config/
│   ├── targets.yml                  → Mapeo worker_id → prometheus_target + node_target
│   ├── prometheus.yml               → Configuración de Prometheus (scrape targets)
│   └── migration.sql                → ALTER TABLE workers ADD COLUMN oc_*
├── install_node_exporter.sh         → Instalar node_exporter en cada worker
├── install_libvirt_exporter.sh      → Instalar libvirt_exporter en cada worker
├── setup_tunnels.sh                 → Configurar tunnels autossh (libvirt + node)
├── Dockerfile
├── docker-compose.yml               → Prometheus + Observability juntos
└── requirements.txt
```

---

## Modelo de overcommit estadístico

### Dos modos de operación por worker

**Modo Baseline** (sin VMs activas):
Acumula Welford del consumo absoluto del host sin carga de VMs.
Esto captura el consumo del SO, hipervisor y procesos del sistema.

**Modo Normal** (con VMs activas + baseline válido):
Resta el baseline del consumo real para aislar el consumo atribuible a las VMs,
luego calcula el ratio de eficiencia y acumula Welford sobre ese ratio.

### Fórmula del ratio de eficiencia (solo CPU)

Para cada worker `j` en cada ciclo:

```
consumo_real_cpu[j]   ← Prometheus (node_exporter del host)
baseline_cpu[j]       ← μ Welford del consumo sin VMs (modo baseline)
nominal_cpu[j]        ← Σ vcores solicitados por VMs ACTIVE (MySQL)

consumo_neto_cpu[j] = max(consumo_real_cpu[j] - baseline_cpu[j], 0)
ratio_cpu[j]         = min(consumo_neto_cpu[j] / nominal_cpu[j], 1.0)
```

El ratio se acota a máximo 1.0: si el consumo neto supera lo solicitado,
no hay sobreasignación posible en esa dimensión.

**RAM ya no sigue este cálculo** — ver sección "RAM: overcommit fijo" más abajo.

### Fórmula del OC (CPU)

```
OC_cpu[j] = 1 / (μ_ratio_cpu[j] + K_CPU · σ_ratio_cpu[j])
```

Donde `μ_ratio_cpu[j]` y `σ_ratio_cpu[j]` se calculan con Welford sobre el
historial de ratios de CPU del worker.

Si `OC_cpu[j]` supera el techo configurado (`OC_CPU_MAX`), se limita.

### RAM: overcommit fijo

`oc_ram` ya no se calcula estadísticamente. `weight_updater.py` escribe
`oc_ram = 2.0` directamente en cada ciclo, sin Welford, sin ratio, sin
baseline aplicado a RAM. Los buffers `buf_ram`/`mu_ram`/`M2_ram` y las
variables `K_RAM`, `OC_RAM_DEFAULT`, `OC_RAM_MAX` quedaron sin efecto para
este cálculo (el baseline de RAM en modo Baseline sigue acumulándose por
compatibilidad, pero no se usa para determinar `oc_ram`).

### Guard de baseline

Para evitar contaminar el buffer Welford con ratios calculados sin baseline
(que sesgarían μ_ratio hacia arriba), el scheduler **no acumula ninguna muestra
de ratio de CPU** hasta que el baseline tenga al menos `BASELINE_MIN_SAMPLES`
muestras. Mientras tanto, el worker mantiene `OC_CPU_DEFAULT` en BD.

### Ejemplo de cálculo (CPU)

Worker 2 con una VM de 1 vCPU:

```
baseline_cpu    = 0.09 cores  ← consumo del host sin VMs
consumo_real    = 0.31 cores  ← Prometheus (node_exporter)
nominal_cpu     = 1.0 cores   ← 1 VM × 1 vcore (MySQL)

consumo_neto    = 0.31 - 0.09 = 0.22 cores
ratio_cpu       = 0.22 / 1.0  = 0.22        → las VMs usan el 22% de lo pedido

Con μ_ratio=0.22 y σ≈0 (poca varianza):
OC_cpu = 1 / (0.22 + 1.0×0) = 4.5×          → limitado a OC_CPU_MAX si aplica
```

`oc_ram` para este mismo worker sería `2.0` fijo, sin este cálculo.

### Algoritmo de Welford con ventana deslizante

Las estadísticas por worker se mantienen con un buffer circular de tamaño fijo
(`WINDOW_SIZE = WINDOW_DAYS × 24h × 3600s / OC_UPDATE_INTERVAL`). Cuando el
buffer está lleno, cada nueva muestra desplaza la más antigua — costo O(1):

```
# Entrada x_new, salida x_old del buffer:
mu_new  = mu_old + (x_new - x_old) / N
M2_new  = M2_old + (x_new - x_old) × (x_new - mu_new + x_old - mu_old)
sigma   = sqrt(M2_new / N)
```

### Factores de seguridad `k_r` por dimensión

| Dimensión | k   | Confianza | Razonamiento |
|-----------|-----|-----------|--------------|
| CPU       | 1.0 | 84%       | Time-shared, throttling recuperable |
| RAM       | —   | —         | **Fijo en 2.0** — ya no usa Welford/ratio/k |
| Disco     | 0.0 | —         | Asignación persistente — sin overcommit (OC=1.0 fijo) |

### Valores de arranque / fijos

| Dimensión | Valor | Origen |
|-----------|-------|--------|
| CPU       | 2.0   | Default de arranque (mientras OC_cpu = NULL en BD); luego calculado |
| RAM       | 2.0   | **Fijo — escrito en cada ciclo, no calculado** |
| Disco     | 1.0   | Fijo — sin overcommit |

---

## Pre-requisitos: setup antes del primer despliegue

### Paso 1 — Instalar `node_exporter` en cada worker

Ejecutar **en cada worker** (Linux Cluster y OpenStack):

```bash
# Copiar el script al worker
scp -P <gw_port> install_node_exporter.sh ubuntu@<ip_gw>:~/

# Conectarse al worker y ejecutar
ssh ubuntu@<ip_gw> -p <gw_port>
chmod +x install_node_exporter.sh
sudo ./install_node_exporter.sh
```

El script instala `node_exporter v1.11.1` desde GitHub releases, configura el
servicio systemd y verifica que el endpoint responde en puerto `9100`.

```bash
# Verificar en el worker
systemctl status node-exporter
curl http://localhost:9100/metrics | grep node_cpu_seconds_total | head -3
```

> **Nota:** `libvirt_exporter` también se instala (puerto 9177) pero dado que las
> VMs corren con QEMU directo (no libvirt), reporta `libvirt_domains 0`. Las
> métricas reales del host provienen de `node_exporter`.

### Paso 2 — Configurar tunnels autossh

Editar `setup_tunnels.sh` con las IPs y puertos reales:

```bash
nano setup_tunnels.sh   # ajustar GW_IP, WORKERS array
chmod +x setup_tunnels.sh
sudo ./setup_tunnels.sh
```

El script crea servicios systemd persistentes (`autossh-worker{N}`) que exponen
**dos tunnels** por worker:
- `libvirt_exporter` (9177) → `localhost:191XX`
- `node_exporter` (9100) → `localhost:192XX`

Ambos escuchan en `0.0.0.0` para que los containers Docker puedan acceder via
`host.docker.internal`.

```
Formato WORKERS: "worker_id:worker_ip:gw_ssh_port:local_libvirt_port:local_node_port"
```

Topología de red:
```
container (host.docker.internal:192XX) → host (0.0.0.0:192XX) → GW:gw_port → worker:9100
```

```bash
# Verificar tunnels
for i in {1..7}; do
    echo -n "worker$i libvirt(191$((76+i))): "
    curl -sf --max-time 2 http://localhost:$((19176+i))/metrics | grep -q "go_goroutines" && echo "OK" || echo "FAIL"
    echo -n "worker$i node   (192$((76+i))): "
    curl -sf --max-time 2 http://localhost:$((19276+i))/metrics | grep -q "node_cpu_seconds_total" && echo "OK" || echo "FAIL"
done
```

### Paso 3 — Actualizar `config/targets.yml`

Copiar la salida del script anterior:

```yaml
workers:
  - worker_id: 1
    prometheus_target: "host.docker.internal:19177"
    node_target:       "host.docker.internal:19277"
  - worker_id: 2
    prometheus_target: "host.docker.internal:19178"
    node_target:       "host.docker.internal:19278"
  # ...
```

### Paso 4 — Actualizar `config/prometheus.yml`

Verificar que los targets coincidan. Hay dos jobs: `libvirt` (puertos 191XX) y
`node` (puertos 192XX):

```yaml
scrape_configs:
  - job_name: 'libvirt'
    static_configs:
      - targets:
          - 'host.docker.internal:19177'
          - 'host.docker.internal:19178'
          # ...

  - job_name: 'node'
    static_configs:
      - targets:
          - 'host.docker.internal:19277'
          - 'host.docker.internal:19278'
          # ...
```

**Regla del rate:** la ventana del `rate()` en las queries PromQL (5m por defecto)
debe ser ≥ 2× el `scrape_interval`. Si `scrape_interval=1m`, `rate([5m])` funciona
correctamente con ~5 muestras por ventana.

### Paso 5 — Migración de BD

Las columnas `oc_cpu`, `oc_ram`, `oc_disco` deben existir en la tabla `workers`.
Si no existen:

```sql
ALTER TABLE workers ADD COLUMN oc_cpu FLOAT NULL;
ALTER TABLE workers ADD COLUMN oc_ram FLOAT NULL;
ALTER TABLE workers ADD COLUMN oc_disco FLOAT NULL;
```

---

## Variables de entorno

| Variable               | Default                   | Descripción |
|------------------------|---------------------------|-------------|
| `PROMETHEUS_URL`       | `http://prometheus:9090`  | URL de Prometheus |
| `OC_UPDATE_INTERVAL`   | `900`                     | Intervalo del scheduler en segundos |
| `WINDOW_DAYS`          | `15`                      | Días de historia en la ventana deslizante |
| `K_CPU`                | `1.0`                     | Factor de seguridad k para CPU |
| `K_RAM`                | `2.0`                     | **Sin efecto** — RAM ya no se calcula estadísticamente (queda por compatibilidad) |
| `OC_CPU_DEFAULT`       | `2.0`                     | OC CPU de arranque (mientras buffer vacío / NULL) |
| `OC_RAM_DEFAULT`       | `1.54`                    | **Sin efecto** — `oc_ram` siempre se escribe como `2.0` |
| `OC_RAM_MAX`           | `1.6`                     | **Sin efecto** — no hay techo que aplicar sobre un valor fijo |
| `OC_CPU_MAX`           | `4.0`                     | Techo para OC CPU |
| `BASELINE_MIN_SAMPLES` | `5`                       | Muestras mínimas de baseline antes de calcular OC |
| `ENABLE_DASHBOARD`     | `false`                   | `true` para habilitar /dashboard |
| `DB_HOST`              | `host.docker.internal`    | Host MySQL |
| `DB_USER`              | `root`                    | Usuario MySQL |
| `DB_PASSWORD`          | `root`                    | Password MySQL |
| `DB_NAME`              | `cloud`                   | Base de datos |
| `TARGETS_FILE`         | `/app/config/targets.yml` | Ruta al archivo de targets |

### Valores de producción recomendados

```yaml
- PROMETHEUS_URL=http://prometheus:9090
- OC_UPDATE_INTERVAL=300       # 5 minutos
- WINDOW_DAYS=15
- K_CPU=1.0
- K_RAM=2.0
- OC_CPU_DEFAULT=2.0
- OC_RAM_DEFAULT=1.54
- OC_RAM_MAX=1.6
- OC_CPU_MAX=4.0
- BASELINE_MIN_SAMPLES=5
- ENABLE_DASHBOARD=true
```

Con `scrape_interval=1m` en `prometheus.yml`.

---

## Endpoints REST

| Método | Ruta                           | Descripción |
|--------|--------------------------------|-------------|
| `GET`  | `/metrics/workers`             | OC factors + uso en vivo de todos los workers |
| `GET`  | `/metrics/workers/{id}`        | Detalle de un worker con VMs activas y estado Welford |
| `GET`  | `/metrics/workers/{id}/window` | Estado de ventana Welford de un worker específico |
| `GET`  | `/metrics/status`              | Estado del scheduler, conectividad, estado Welford + baseline |
| `GET`  | `/dashboard`                   | Dashboard web (solo si `ENABLE_DASHBOARD=true`) |
| `GET`  | `/health`                      | Liveness check |

---

## Dashboard

El dashboard muestra en tiempo real:

**Sección SCHEDULER:** estado del scheduler, ciclos completados, último error.

**Sección SUMMARY:** targets up/total, cycles run, OC source (live/default), last error.

**Sección WORKERS:** tarjeta por worker con CPU%, RAM% en vivo, y factores OC
(con badge `default`, `live` o `fixed`). `oc_ram` siempre muestra `fixed`.

**Sección WORKER WELFORD STATE:** tabla con μ_ratio y σ_ratio de CPU por worker,
baseline CPU/RAM, número de muestras, barra de progreso de la ventana, y
estado (Baseline / Warming up / Live). Ya no reporta μ_ratio/σ_ratio de RAM
(el campo se muestra como `"fixed"`).

---

## Despliegue

Una vez completados los pasos de pre-requisitos:

```bash
# Levantar Prometheus + Observability
docker compose up -d --build

# Verificar
curl http://localhost:8006/health
curl http://localhost:8006/metrics/workers
curl http://localhost:8006/metrics/status

# Ver dashboard
# http://localhost:8006/dashboard

# Ver logs
docker compose logs -f observability
docker compose logs -f prometheus
```

---

## Integración con el Slice Manager

El Slice Manager lee `oc_cpu`, `oc_ram`, `oc_disco` desde la tabla `workers` al
construir el Servers' State. `oc_ram` siempre vale `2.0` (fijo). El cambio en
`placement_worker.py` es:

```python
# Antes (F_OP escalar hardcodeado):
F_OP = 1 / 0.65
C_i = (3*cpu + 5*ram_gb + 1*disk_gb) * F_OP

# Después (factores por dimensión desde BD):
oc_cpu   = worker.oc_cpu   or OC_CPU_DEFAULT    # 2.0
oc_ram   = worker.oc_ram   or OC_RAM_DEFAULT    # 1.54 (fallback; en BD normalmente ya es 2.0)
oc_disco = worker.oc_disco or OC_DISCO_DEFAULT  # 1.0

C_i = 3*(cpu * oc_cpu) + 5*(ram_gb * oc_ram) + 1*(disk_gb * oc_disco)
```

La estructura de pesos β:α:γ = 5:3:1 se mantiene idéntica. VM Placement no
requiere cambios — sigue recibiendo `{worker_id, disponible}` escalar.

Además, antes de construir el Servers' State, el Slice Manager consulta
`GET /metrics/workers` de este servicio para aplicar un **gate de elegibilidad
por uso en vivo**: workers con `live_ram_usage_pct > 90` o
`live_cpu_usage_pct > 95` se excluyen del placement (no reciben nuevas VMs).
Ver `READMEslicemanager.md` para el detalle del filtro.

---

## Notas operacionales

- **Baseline primero:** al desplegar por primera vez o reiniciar el container,
  es recomendable dejar el sistema sin VMs activas durante algunos ciclos para
  que el baseline se acumule (mínimo `BASELINE_MIN_SAMPLES` muestras). Sin
  baseline válido, los workers con VMs mantienen los defaults.

- **Estado en memoria:** los buffers Welford (`_state` y `_baseline`) viven en
  memoria del container. Al reiniciar se pierden y comienzan a acumularse desde
  cero. Los valores OC en BD persisten y se usan como fallback.

- **Prometheus conserva el histórico** de métricas raw en el volumen
  `prometheus_data` — persiste entre reinicios del container.

- **Cambio de VMs:** cuando se despliegan o terminan VMs en un worker, el buffer
  Welford puede contener ratios calculados con una configuración de VMs diferente.
  Con suficientes ciclos, las muestras viejas salen de la ventana deslizante y μ
  converge al valor correcto.

- **OC por debajo de 1.0:** si `ratio > 1.0` en alguna muestra (consumo neto
  supera lo nominal), se acota a 1.0. Esto previene OC < 1.0 en dimensiones
  individuales.

- **Healthcheck:** el container usa `wget -qO- http://localhost:8000/health`
  (no `curl`, que no está instalado en la imagen slim).
