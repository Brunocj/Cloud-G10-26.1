# Compute Provisioner

Microservicio encargado del aprovisionamiento computacional del sistema de slices.
Recibe órdenes del módulo de colas, crea y destruye VMs en los workers via SSH+QEMU/KVM,
y publica el resultado de vuelta al encolador.

---

## Responsabilidades

- Crear discos QCOW2 con thin provisioning (backing file = imagen base) en cada worker.
- Lanzar procesos QEMU/KVM en los workers via SSH.
- Asignar puertos VNC de forma centralizada, garantizando que no haya colisiones.
- Destruir VMs y sus discos cuando se elimina un slice.
- Reintentar ante fallos transitorios.
- Notificar el resultado (éxito / error / parcial) al módulo de colas.

**No** decide en qué worker va cada VM — eso es responsabilidad del VM Placement.  
**No** configura red, no crea TAP interfaces, no gestiona VLANs ni OVS — eso es responsabilidad del Network Orchestrator.  
**No** asigna el puerto VNC desde el mensaje — lo decide internamente este módulo.

---

## Arquitectura del módulo

```
main.py
 ├── api/health.py              → GET /health (healthcheck HTTP)
 ├── core/
 │    ├── config.py             → Variables de entorno (Settings)
 │    ├── worker.py             → Loop principal: consume queue → despacha
 │    └── logging_config.py    → Configuración de logs
 ├── models/
 │    └── schemas.py            → Contratos de entrada/salida (Pydantic)
 ├── services/
 │    ├── provisioner.py        → Orquestación deploy/destroy (lógica central)
 │    ├── qemu_executor.py      → Comandos QEMU/KVM sobre el worker
 │    ├── ssh_client.py         → Wrapper SSH con llave PEM en memoria (Paramiko)
 │    ├── vnc_port_manager.py   → Asignación thread-safe de puertos VNC por worker
 │    └── queue_client.py       → Consumo/publicación Redis
 └── utils/
      └── image_resolver.py    → Resolución de rutas de imágenes (adaptable a BD)
```

---

## Flujo de mensajes

```
Módulo de Colas
    │
    │  queue:compute:deploy
    │  { slice_id, request_id, vms: [{vm_id, worker_ip, ssh_user,
    │    ssh_private_key, vcpus, ram_mb, image_name, priority}] }
    ▼
Compute Provisioner
    │
    ├─ Asigna puerto VNC por worker (VNCPortManager)
    ├─ SSH → workerN: qemu-img create -f qcow2 -b base.qcow2 vm-X.qcow2
    ├─ SSH → workerN: qemu-system-x86_64 -enable-kvm -name vm-X ... -daemonize
    └─ SSH → workerN: pgrep -f "name vm-X" → obtener PID
    │
    │  queue:compute:result
    │  { slice_id, request_id, status, vms: [{vm_id, worker_ip, pid, vnc_port}] }
    ▼
Módulo de Colas  →  Slice Manager
```

Para destroy, el flujo es análogo usando `queue:compute:destroy`.

---

## Formato de mensajes

### Entrada: DEPLOY_SLICE

El puerto VNC **no viene en el mensaje** — es asignado internamente por este módulo.

```json
{
  "slice_id": "slice-abc123",
  "request_id": "req-xyz789",
  "vms": [
    {
      "vm_id": "vm-1",
      "worker_ip": "192.168.1.101",
      "ssh_user": "ubuntu",
      "ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "vcpus": 2,
      "ram_mb": 1024,
      "image_name": "ubuntu-22.04.qcow2",
      "priority": 0
    }
  ]
}
```

### Entrada: DESTROY_SLICE

```json
{
  "slice_id": "slice-abc123",
  "request_id": "req-xyz789"
}
```

### Salida: resultado deploy (éxito)

```json
{
  "event": "DEPLOY_RESULT",
  "slice_id": "slice-abc123",
  "request_id": "req-xyz789",
  "status": "success",
  "vms": [
    { "vm_id": "vm-1", "worker_ip": "192.168.1.101", "pid": 14823, "vnc_port": 5901 }
  ],
  "failed_vms": []
}
```

### Salida: resultado deploy (error parcial)

```json
{
  "event": "DEPLOY_RESULT",
  "slice_id": "slice-abc123",
  "request_id": "req-xyz789",
  "status": "partial",
  "vms": [
    { "vm_id": "vm-1", "worker_ip": "192.168.1.101", "pid": 14823, "vnc_port": 5901 }
  ],
  "failed_vms": [
    { "vm_id": "vm-2", "worker_ip": "192.168.1.102", "pid": null, "vnc_port": null, "error": "qemu process did not start" }
  ]
}
```

### Valores de `status`

| Valor | Significado |
|-------|-------------|
| `success` | Todas las VMs operaron correctamente |
| `error` | Ninguna VM pudo completar la operación |
| `partial` | Algunas VMs OK, otras fallaron |

---

## Gestión de puertos VNC

El módulo asigna los puertos VNC internamente a través del `VNCPortManager`, un singleton
thread-safe que garantiza que nunca se repita un puerto en el mismo worker.

Antes de asignar un puerto, el manager consulta al worker qué procesos QEMU están
activos para detectar puertos realmente en uso. Adicionalmente mantiene un registro
en memoria para coordinar asignaciones concurrentes dentro del mismo deploy.

El rango de puertos disponible es **5901–5999** (displays VNC 1–99 por worker).

Si todos los reintentos de una VM fallan, su puerto VNC se libera automáticamente.

---

## Variables de entorno

Copiar `.env.example` a `.env` y ajustar:

| Variable | Default | Descripción |
|----------|---------|-------------|
| `REDIS_HOST` | `redis` | Host del broker Redis |
| `REDIS_PORT` | `6379` | Puerto Redis |
| `QUEUE_DEPLOY` | `queue:compute:deploy` | Queue de entrada para deploy |
| `QUEUE_DESTROY` | `queue:compute:destroy` | Queue de entrada para destroy |
| `QUEUE_RESULT` | `queue:compute:result` | Queue de salida de resultados |
| `SSH_TIMEOUT` | `30` | Timeout de conexión SSH (segundos) |
| `SSH_MAX_RETRIES` | `3` | Reintentos por VM ante fallo |
| `SSH_RETRY_DELAY` | `5` | Segundos entre reintentos |
| `IMAGES_BASE_DIR` | `/images` | Directorio de imágenes base en workers |
| `VMS_BASE_DIR` | `/vms` | Directorio de discos VM en workers |
| `MAX_CONCURRENT_WORKERS` | `10` | Workers procesados en paralelo |
| `LOG_LEVEL` | `INFO` | Nivel de logging |
| `HEALTH_PORT` | `8080` | Puerto del healthcheck HTTP |

---

## Prerequisitos en los workers

Antes de desplegar, cada worker debe tener:

1. **QEMU/KVM instalado**
   ```bash
   apt install -y qemu-system-x86 qemu-utils
   ```

2. **Acceso SSH con llave PEM** — la llave privada viene en cada mensaje desde
   el Slice Manager. La llave pública debe estar en `~/.ssh/authorized_keys` del worker.

3. **Directorios creados**
   ```bash
   mkdir -p /images /vms
   ```

4. **Imágenes base presentes** en `/images/`
   ```bash
   ls /images/ubuntu-22.04.qcow2
   ```

---

## Despliegue con Docker

```bash
# 1. Copiar configuración
cp .env.example .env

# 2. Levantar
docker compose up -d

# 3. Verificar
curl http://localhost:8081/health
# → {"status": "ok", "redis": true}

# 4. Logs
docker compose logs -f compute-provisioner
```

---

## Ejecución local (sin Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Ajustar REDIS_HOST=localhost si Redis corre localmente

python main.py
```

---

## Tests

```bash
pip install pytest pytest-mock
pytest tests/ -v
```

Los tests usan mocks para SSH, Redis y VNCPortManager — no requieren workers reales ni broker activo.

---

## Nota sobre imágenes (evolución futura)

Actualmente las imágenes base se asumen presentes en todos los workers.
La función `app/utils/image_resolver.py:get_image_path()` es el único punto
a modificar cuando se migre a distribución dinámica desde una BD.
El resto del código no cambia.

---

## Identificación de procesos en los workers

Cada proceso QEMU se nombra con el patrón `{vm_id}-{slice_id}`, por ejemplo:
`vm-1-slice-abc123`. Esto permite buscarlo manualmente:

```bash
pgrep -f "name vm-1-slice-abc123"
# → 14823

ps aux | grep "vm-1-slice-abc123"
```
