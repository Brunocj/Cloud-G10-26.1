# Queue Manager

Microservicio que actúa como orquestador central del sistema de slices.
Recibe solicitudes del Slice Manager, coordina la ejecución ordenada de los módulos
internos (Compute Provisioner, y en el futuro Network Orchestrator), y notifica
el resultado final de vuelta al Slice Manager.

---

## Responsabilidades

- Recibir solicitudes de despliegue y destrucción de slices desde el Slice Manager.
- Orquestar el orden de ejecución entre módulos (actualmente solo Compute Provisioner).
- Persistir el estado de cada operación en NATS JetStream KV para futura implementación de rollback.
- Notificar el resultado final al Slice Manager vía NATS.

**No** ejecuta comandos en los workers — eso es responsabilidad del Compute Provisioner.  
**No** configura red — eso es responsabilidad del Network Orchestrator (futuro).  
**No** decide en qué worker va cada VM — eso es responsabilidad del VM Placement.

---

## Arquitectura del módulo

```
main.py
 ├── api/health.py              → GET /health (healthcheck HTTP)
 ├── core/
 │    ├── config.py             → Variables de entorno (Settings)
 │    └── logging_config.py    → Configuración de logs
 ├── models/
 │    └── schemas.py            → Contratos de entrada/salida (Pydantic)
 └── services/
      ├── nats_client.py        → Conexión NATS (JetStream + core NATS)
      ├── orchestrator.py       → Lógica de orquestación de pasos
      └── handlers.py           → Handlers de mensajes NATS
```

---

## Protocolo de comunicación

Este módulo usa dos mecanismos NATS distintos según el propósito:

**JetStream** — para mensajes persistentes que deben sobrevivir reinicios:
- `slice.deploy` y `slice.destroy` → entrada desde el Slice Manager
- `slice.result` → salida hacia el Slice Manager
- KV bucket `slice-state` → estado interno de operaciones en curso

**Core NATS (request/reply)** — para comunicación síncrona con módulos internos:
- `compute.deploy` → request al Compute Provisioner, espera respuesta directa
- `compute.destroy` → request al Compute Provisioner, espera respuesta directa

La razón de esta separación es que `compute.*` no debe pasar por el stream — si lo hiciera, el ACK de JetStream llegaría como respuesta en vez del resultado real del Compute Provisioner.

---

## Flujo de mensajes

```
Slice Manager
    │
    │  JetStream → slice.deploy / slice.destroy
    ▼
Queue Manager
    │
    ├─ Persiste estado en NATS KV
    ├─ Paso 1: core NATS request → compute.deploy
    │           espera respuesta directa del Compute Provisioner
    │
    │  (futuro) Paso 2: core NATS request → network.deploy
    │                   espera respuesta directa del Network Orchestrator
    │
    ├─ Elimina estado del KV
    └─ core NATS publish → slice.result
    │
    ▼
Slice Manager
```

---

## Formato de mensajes

### Entrada: slice.deploy

```json
{
  "slice_id": "slice-test-001",
  "request_id": "req-001",
  "vms": [
    {
      "vm_id": "vm-1",
      "worker_ip": "10.0.10.2",
      "ssh_user": "ubuntu",
      "ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "vcpus": 1,
      "ram_mb": 256,
      "image_name": "cirros-0.5.1-x86_64-disk.img",
      "priority": 0
    }
  ]
}
```

### Entrada: slice.destroy

```json
{
  "slice_id": "slice-test-001",
  "request_id": "req-002"
}
```

### Salida: slice.result (deploy exitoso)

```json
{
  "slice_id": "slice-test-001",
  "request_id": "req-001",
  "status": "success",
  "vms": [
    { "vm_id": "vm-1", "worker_ip": "10.0.10.2", "pid": 14823, "vnc_port": 5901 }
  ],
  "failed_vms": []
}
```

### Salida: slice.result (destroy exitoso)

```json
{
  "slice_id": "slice-test-001",
  "request_id": "req-002",
  "status": "success",
  "destroyed_vms": ["vm-1"],
  "failed_vms": []
}
```

### Valores de `status`

| Valor | Significado |
|-------|-------------|
| `success` | Todos los pasos completados correctamente |
| `error` | Fallo total — ningún paso completado |
| `partial` | Algunos recursos OK, otros fallaron |

---

## Variables de entorno

Copiar `.env.example` a `.env` y ajustar:

| Variable | Default | Descripción |
|----------|---------|-------------|
| `NATS_URL` | `nats://nats:4222` | URL del servidor NATS |
| `SUBJECT_DEPLOY` | `slice.deploy` | Subject de entrada para deploy |
| `SUBJECT_DESTROY` | `slice.destroy` | Subject de entrada para destroy |
| `SUBJECT_RESULT` | `slice.result` | Subject de salida de resultados |
| `SUBJECT_COMPUTE_DEPLOY` | `compute.deploy` | Subject hacia el Compute Provisioner (deploy) |
| `SUBJECT_COMPUTE_DESTROY` | `compute.destroy` | Subject hacia el Compute Provisioner (destroy) |
| `JS_STREAM_NAME` | `SLICES` | Nombre del stream JetStream (solo captura `slice.*`) |
| `JS_KV_BUCKET` | `slice-state` | Bucket KV para persistir estado de operaciones |
| `COMPUTE_TIMEOUT` | `300` | Segundos máximos para esperar respuesta del Compute Provisioner |
| `LOG_LEVEL` | `INFO` | Nivel de logging |
| `HEALTH_PORT` | `8080` | Puerto del healthcheck HTTP |

---

## Despliegue con Docker

```bash
# 1. Copiar configuración
cp .env.example .env

# 2. Levantar
docker compose up -d

# 3. Verificar
curl http://localhost:8083/health
# → {"status": "ok", "nats": true}

# 4. Logs
docker compose logs -f queue-manager
```

---

## Prueba manual sin Slice Manager

Instala la NATS CLI y publica mensajes directamente:

```bash
# Suscribirse para ver resultados (en otra terminal)
nats sub slice.result

# Publicar un deploy
nats pub slice.deploy '{
  "slice_id": "slice-001",
  "request_id": "req-001",
  "vms": [{
    "vm_id": "vm-1",
    "worker_ip": "10.0.10.2",
    "ssh_user": "ubuntu",
    "ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
    "vcpus": 1,
    "ram_mb": 256,
    "image_name": "cirros-0.5.1-x86_64-disk.img"
  }]
}'

# Publicar un destroy
nats pub slice.destroy '{
  "slice_id": "slice-001",
  "request_id": "req-002"
}'
```

---

## Ejecución local (sin Docker)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Ajustar NATS_URL=nats://localhost:4222

python main.py
```

---

## Tests

```bash
pip install pytest pytest-asyncio pytest-mock
pytest tests/ -v --asyncio-mode=auto
```

Los tests mockean el NATSManager — no requieren NATS activo.

---

## Nota importante sobre el stream SLICES

El stream `SLICES` solo captura `slice.*`. Los subjects `compute.*` usan
core NATS request/reply directamente y **no pasan por el stream**.

Si el stream fue creado previamente con `compute.*` en su configuración,
hay que eliminarlo antes de levantar el servicio:

```bash
nats stream delete SLICES
```

El Queue Manager lo recreará automáticamente con la configuración correcta.

---

## Extensibilidad: agregar Network Orchestrator

En `app/services/orchestrator.py`, método `deploy()`, descomentar:

```python
# Paso 2: Network
network_result = await self._step_network_deploy(request, successful)
if network_result is None:
    return await self._fail_deploy(state, "Timeout: Network Orchestrator no respondió")
state.completed_steps.append(OperationStep.NETWORK)
await self._save_state(state)
```

E implementar `_step_network_deploy()` siguiendo el mismo patrón que
`_step_compute_deploy()`. El resto del código no cambia.

---

## Puertos expuestos

| Puerto | Descripción |
|--------|-------------|
| `4222` | NATS — clientes |
| `8222` | NATS — monitoring HTTP (`/healthz`, `/varz`, `/connz`) |
| `8083` | Queue Manager — healthcheck |
