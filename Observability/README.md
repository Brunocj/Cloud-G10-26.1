# Observability — PUCP Cloud Orchestrator

Microservicio independiente que implementa el feedback loop de VM Placement.
Consulta Prometheus periódicamente, calcula estadísticas de uso por VM usando
una ventana deslizante de 15 días con el algoritmo de Welford incremental, y
escribe los factores de overcommit (OC) en MySQL para que el Slice Manager los
use en el siguiente placement.

---

## Responsabilidades

- Consultar métricas instantáneas de CPU y RAM por VM desde Prometheus (vía `libvirt_exporter`).
- Mantener una ventana deslizante de 15 días de muestras por VM con Welford incremental — O(1) por ciclo.
- Agregar estadísticas de VM a worker: `μ_r[j] = Σμ_r(vm)`, `σ_r[j] = √(Σσ²_r(vm))`.
- Calcular factores OC por worker: `OC_r[j] = C_nominal_r[j] / (μ_r[j] + k_r · σ_r[j])`.
- Escribir `oc_cpu`, `oc_ram`, `oc_disco` en la tabla `workers` de MySQL.
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
│   ├── database.py                  → ORM SQLAlchemy (Worker, Vm)
│   ├── targets.py                   → Carga y cachea targets.yml
│   └── services/
│       ├── prometheus_client.py     → Queries PromQL instantáneas (httpx)
│       ├── weight_updater.py        → Welford sliding window + cálculo OC + escritura BD
│       └── scheduler.py             → Background loop periódico
├── routers/
│   ├── metrics_router.py            → GET /metrics/workers, /metrics/workers/{id},
│   │                                   /metrics/vms/{id}, /metrics/status
│   └── dashboard_router.py          → GET /dashboard (solo si ENABLE_DASHBOARD=true)
├── static/
│   └── dashboard.html               → Dashboard de monitoreo (auto-refresh 15s)
├── config/
│   ├── targets.yml                  → Mapeo worker_id → prometheus_target
│   ├── prometheus.yml               → Configuración de Prometheus (scrape targets)
│   └── migration.sql                → ALTER TABLE workers ADD COLUMN oc_*
├── install_libvirt_exporter.sh      → Instalar exporter en cada worker
├── setup_tunnels.sh                 → Configurar tunnels autossh en server1
├── Dockerfile
├── docker-compose.yml               → Prometheus + Observability juntos
└── requirements.txt
```

---

## Modelo de overcommit estadístico

Para cada worker `j` y dimensión `r`:

```
OC_r[j] = C_nominal_r[j] / (μ_r[j] + k_r · σ_r[j])
```

Donde `μ_r[j]` y `σ_r[j]` se calculan agregando las estadísticas de todas las
VMs activas en el worker:

```
μ_r[j]  = Σ μ_r(vm_i)
σ_r[j]  = √(Σ σ²_r(vm_i))
```

### Algoritmo de Welford con ventana deslizante

Las estadísticas por VM se mantienen con un buffer circular de tamaño fijo
(`WINDOW_SIZE = WINDOW_DAYS × 24h × ciclos/h`). Cuando el buffer está lleno,
cada nueva muestra desplaza la más antigua — el costo es O(1):

```
# Entrada x_new, salida x_old del buffer:
mu_new  = mu_old + (x_new - x_old) / N
M2_new  = M2_old + (x_new - x_old) × (x_new - mu_new + x_old - mu_old)
sigma   = sqrt(M2_new / N)
```

Con `WINDOW_DAYS=15` y `OC_UPDATE_INTERVAL=900s`:
```
WINDOW_SIZE = 15 × 24 × 4 = 1440 muestras por VM
```

### Factores de seguridad `k_r` por dimensión

| Dimensión | k   | Confianza | Razonamiento |
|-----------|-----|-----------|--------------|
| CPU       | 1.0 | 84%       | Time-shared, throttling recuperable |
| RAM       | 2.0 | 97.7%     | OOM-killer irreversible — conservador |
| Disco     | 0.0 | —         | Asignación persistente — sin overcommit |

### Valores de arranque (mientras OC = NULL en BD)

| Dimensión | Default |
|-----------|---------|
| CPU       | 2.0     |
| RAM       | 1.54    |
| Disco     | 1.0     |

---

## Pre-requisitos: setup antes del primer despliegue

### Paso 1 — Instalar `libvirt_exporter` en cada worker

Ejecutar **en cada worker** (Linux Cluster y OpenStack):

```bash
# Copiar el script al worker
scp install_libvirt_exporter.sh ubuntu@<ip_gw> -p <gw_port>:~/

# Conectarse al worker
ssh ubuntu@<ip_gw> -p <gw_port>

# Dar permisos y ejecutar
chmod +x install_libvirt_exporter.sh
sudo ./install_libvirt_exporter.sh --type linux       # workers Linux Cluster
sudo ./install_libvirt_exporter.sh --type openstack   # workers OpenStack
```

El script instala `prometheus-libvirt-exporter v2.4.0` desde GitHub releases,
configura el servicio systemd y verifica que el endpoint responde en puerto `9177`.

```bash
# Verificar en el worker
systemctl status prometheus-libvirt-exporter
curl http://localhost:9177/metrics | head -5
```

### Paso 2 — Configurar tunnels autossh en server1

Editar la sección `WORKER CONFIGURATION` y `GW_IP` en el script:

```bash
nano setup_tunnels.sh   # ajustar GW_IP, IPs internas y puertos GW de cada worker
chmod +x setup_tunnels.sh
sudo ./setup_tunnels.sh
```

El script crea servicios systemd persistentes (`autossh-worker{N}`) que exponen
cada worker como `localhost:191XX` en server1. Al finalizar imprime el bloque
`workers:` listo para copiar en `config/targets.yml`.

> **Topología:** todos los workers son accesibles directamente desde server1
> vía el GW con port forwarding — no hay saltos intermedios.
> ```
> server1 → GW:<gw_port> → worker:9177
> ```

```bash
# Verificar tunnels
for i in {1..7}; do
    echo -n "worker$i → localhost:$((19176+i)): "
    curl -sf --max-time 3 http://localhost:$((19176+i))/metrics \
        | grep -q "go_goroutines" && echo "OK" || echo "FAIL"
done
```

> **Nota sobre autenticación SSH:** si el GW autentica con key, asegúrate de
> que el service file incluye `-i /home/ubuntu/.ssh/id_ed25519`. El script lo
> configura automáticamente si `GW_KEY` está definido.

### Paso 3 — Actualizar `config/targets.yml`

Copiar la salida del script anterior:

```yaml
workers:
  - worker_id: 1
    prometheus_target: "localhost:19177"
  - worker_id: 2
    prometheus_target: "localhost:19178"
  # ...
```

### Paso 4 — Actualizar `config/prometheus.yml`

Verificar que los targets en `prometheus.yml` coinciden con los puertos locales
configurados en el paso anterior:

```yaml
scrape_configs:
  - job_name: 'libvirt'
    static_configs:
      - targets:
          - 'localhost:19177'
          - 'localhost:19178'
          # ...
```

### Paso 5 — Migración de BD

Ejecutar una sola vez:

```bash
mysql -u root -p cloud < config/migration.sql
```

---

## Variables de entorno

| Variable               | Default                   | Descripción |
|------------------------|---------------------------|-------------|
| `PROMETHEUS_URL`       | `http://localhost:9090`   | URL de Prometheus |
| `OC_UPDATE_INTERVAL`   | `900`                     | Intervalo del scheduler en segundos (15 min) |
| `WINDOW_DAYS`          | `15`                      | Días de historia en la ventana deslizante |
| `K_CPU`                | `1.0`                     | Factor de seguridad k para CPU |
| `K_RAM`                | `2.0`                     | Factor de seguridad k para RAM |
| `OC_CPU_DEFAULT`       | `2.0`                     | OC CPU de arranque (mientras buffer vacío) |
| `OC_RAM_DEFAULT`       | `1.54`                    | OC RAM de arranque |
| `OC_RAM_MAX`           | `1.6`                     | Techo para OC RAM |
| `OC_CPU_MAX`           | `4.0`                     | Techo para OC CPU |
| `VM_ACTIVATION_HOURS`  | `36`                      | Horas antes de forzar activación por tiempo |
| `ENABLE_DASHBOARD`     | `false`                   | `true` para habilitar /dashboard |
| `DB_HOST`              | `host.docker.internal`    | Host MySQL |
| `DB_USER`              | `root`                    | Usuario MySQL |
| `DB_PASSWORD`          | `root`                    | Password MySQL |
| `DB_NAME`              | `cloud`                   | Base de datos |
| `TARGETS_FILE`         | `/app/config/targets.yml` | Ruta al archivo de targets |

---

## Endpoints REST

| Método | Ruta                      | Descripción |
|--------|---------------------------|-------------|
| `GET`  | `/metrics/workers`        | OC factors + uso en vivo de todos los workers |
| `GET`  | `/metrics/workers/{id}`   | Detalle de un worker con VMs y estado de ventana |
| `GET`  | `/metrics/vms/{vm_id}`    | Estado de ventana Welford de una VM |
| `GET`  | `/metrics/status`         | Estado del scheduler y conectividad de targets |
| `GET`  | `/dashboard`              | Dashboard web (solo si `ENABLE_DASHBOARD=true`) |
| `GET`  | `/health`                 | Liveness check |

---

## Despliegue

Una vez completados los pasos de pre-requisitos:

```bash
# Levantar Prometheus + Observability
docker compose up -d --build

# Con dashboard habilitado
# Editar docker-compose.yml: ENABLE_DASHBOARD=true
docker compose up -d --build

# Verificar
curl http://localhost:8000/health
curl http://localhost:8000/metrics/workers
curl http://localhost:8000/metrics/status

# Ver dashboard (si habilitado)
# http://localhost:8000/dashboard

# Ver logs
docker compose logs -f observability
docker compose logs -f prometheus
```

## Notas operacionales

- Los factores OC usan los valores default hasta que el buffer de cada VM
  acumule suficientes muestras. El endpoint `/metrics/vms/{vm_id}` muestra
  `window_pct` — porcentaje de llenado de la ventana.
- Si el microservicio se reinicia, el estado Welford se pierde y el buffer
  comienza a llenarse de nuevo desde cero. Los valores OC vuelven a default
  hasta que haya suficientes muestras.
- Prometheus conserva el histórico de métricas raw en el volumen
  `prometheus_data` — persiste entre reinicios del container.
