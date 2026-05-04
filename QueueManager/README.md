# Queue Manager — PUCP Cloud Orchestrator

Microservicio que actúa como orquestador central del sistema de slices.
Recibe solicitudes del Slice Manager, coordina la ejecución ordenada de los módulos
internos (Compute Provisioner y Network Orchestrator), y notifica el resultado
final de vuelta al Slice Manager.

---

## Responsabilidades

- Recibir solicitudes de despliegue y destrucción de slices desde el Slice Manager.
- Orquestar el orden de ejecución entre módulos: primero Compute, luego Network.
- Persistir el estado de cada operación en NATS JetStream KV.
- Notificar el resultado final al Slice Manager vía NATS.
- Alojar el servidor NATS que usan todos los módulos internos.

**No** ejecuta comandos en los workers — eso es responsabilidad del Compute Provisioner.  
**No** configura red — eso es responsabilidad del Network Orchestrator.  
**No** decide en qué worker va cada VM — eso es responsabilidad del VM Placement.

---

## Arquitectura del módulo

```
main.py
 ├── api/health.py              → GET /health (healthcheck HTTP, puerto 8082)
 ├── core/
 │    └── config.py             → Variables de entorno (Settings)
 ├── models/
 │    └── schemas.py            → Contratos de entrada/salida (Pydantic)
 └── services/
      ├── nats_client.py        → Conexión NATS (JetStream + core NATS + KV)
      ├── orchestrator.py       → Lógica de orquestación de pasos
      └── handlers.py           → Handlers de mensajes NATS (slice.deploy / slice.destroy)
```

---

## Protocolo de comunicación

Este módulo usa dos mecanismos NATS distintos según el propósito:

**JetStream** — para mensajes persistentes del Slice Manager:
- `slice.deploy` y `slice.destroy` → entrada desde el Slice Manager (persistidos en stream `SLICES`)
- `slice.result` → salida hacia el Slice Manager (core NATS publish)
- KV bucket `slice-state` → estado interno de operaciones en curso (TTL: 3600s)

**Core NATS (request/reply)** — para comunicación síncrona con módulos internos:
- `compute.deploy` / `compute.destroy` → request al Compute Provisioner, espera respuesta directa
- `network.deploy` / `network.destroy` → request al Network Orchestrator, espera respuesta directa

La razón de esta separación: `compute.*` y `network.*` no deben pasar por el stream JetStream.
Si lo hicieran, el ACK de JetStream llegaría como respuesta en vez del resultado real del módulo.

---

## Flujo de mensajes

### Deploy

```
Slice Manager
    │
    │  JetStream → slice.deploy
    ▼
Queue Manager
    │
    ├─ ACK inmediato al stream
    ├─ Persiste estado en NATS KV
    │
    ├─ Paso 1: core NATS request → compute.deploy  (timeout: 300s)
    │           espera respuesta directa del Compute Provisioner
    │           si status == "error" → falla y notifica
    │
    ├─ Paso 2: core NATS request → network.deploy  (timeout: 60s)
    │           espera respuesta directa del Network Orchestrator
    │           si status != "success" → falla y notifica
    │
    ├─ Elimina estado del KV
    └─ core NATS publish → slice.result
    │
    ▼
Slice Manager
```

### Destroy

```
Slice Manager
    │
    │  JetStream → slice.destroy
    ▼
Queue Manager
    │
    ├─ ACK inmediato al stream
    ├─ Persiste estado en NATS KV
    │
    ├─ Paso 0: core NATS request → network.destroy  (timeout: 60s)
    │           limpia VLANs y puertos OVS
    │           si no responde: warning y continúa (no bloquea)
    │
    ├─ Paso 1: core NATS request → compute.destroy  (timeout: 300s)
    │           destruye VMs
    │
    ├─ Elimina estado del KV
    └─ core NATS publish → slice.result
    │
    ▼
Slice Manager
```

> El destroy limpia la red **antes** de destruir las VMs para garantizar
> que los puertos OVS se desconecten mientras las interfaces TAP todavía existen.

---

## Formato de mensajes

### Entrada: slice.deploy

```json
{
  "slice_id": "slice-abc123",
  "request_id": "req-xyz789",
  "vms": [
    {
      "vm_id": "vm-1",
      "worker_ip": "10.0.10.2",
      "ssh_user": "ubuntu",
      "ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "vcpus": 1,
      "ram_mb": 256,
      "disk_gb": 10,
      "image_path": "/images/cirros-0.5.1-x86_64-disk.img",
      "vnc_port": 5901,
      "vnc_display": 1,
      "tap_interfaces": [
        { "tap_name": "tap-vm1-0", "mac": "52:54:00:A3:C7:00" }
      ],
      "priority": 0
    }
  ],
  "links": [
    {
      "connection_id": "conn-001",
      "vlan_id": 100,
      "vm1_id": "vm-1",
      "vm1_worker_ip": "10.0.10.2",
      "vm1_tap": "tap-vm1-0",
      "vm1_ssh_user": "ubuntu",
      "vm1_ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "vm1_security_rules": [{ "allow_port": 22, "protocol": "tcp" }],
      "vm2_id": "vm-2",
      "vm2_worker_ip": "10.0.10.3",
      "vm2_tap": "tap-vm2-0",
      "vm2_ssh_user": "ubuntu",
      "vm2_ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "vm2_security_rules": []
    }
  ]
}
```

### Entrada: slice.destroy

```json
{
  "slice_id": "slice-abc123",
  "request_id": "req-xyz789",
  "vms": [
    {
      "vm_id": "vm-1",
      "worker_ip": "10.0.10.2",
      "ssh_user": "ubuntu",
      "ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "vcpus": 1,
      "ram_mb": 256,
      "disk_gb": 10,
      "image_path": "/images/cirros-0.5.1-x86_64-disk.img",
      "vnc_port": 5901,
      "vnc_display": 1,
      "tap_interfaces": [
        { "tap_name": "tap-vm1-0", "mac": "52:54:00:A3:C7:00" }
      ]
    }
  ],
  "links": [
    {
      "connection_id": "conn-001",
      "vlan_id": 100,
      "vm1_worker_ip": "10.0.10.2",
      "vm1_tap": "tap-vm1-0",
      "vm1_ssh_user": "ubuntu",
      "vm1_ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "vm2_worker_ip": "10.0.10.3",
      "vm2_tap": "tap-vm2-0",
      "vm2_ssh_user": "ubuntu",
      "vm2_ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
    }
  ]
}
```

> El destroy incluye la "receta" completa (vms + links) para que los módulos
> internos puedan limpiar sin depender de estado externo.

### Salida: slice.result (deploy exitoso)

```json
{
  "slice_id": "slice-abc123",
  "request_id": "req-xyz789",
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
  "slice_id": "slice-abc123",
  "request_id": "req-xyz789",
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
| `partial` | Compute OK pero algunas VMs fallaron |

---

## Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `NATS_URL` | `nats://nats:4222` | URL del servidor NATS |
| `SUBJECT_DEPLOY` | `slice.deploy` | Subject de entrada para deploy |
| `SUBJECT_DESTROY` | `slice.destroy` | Subject de entrada para destroy |
| `SUBJECT_RESULT` | `slice.result` | Subject de salida de resultados |
| `SUBJECT_COMPUTE_DEPLOY` | `compute.deploy` | Subject hacia el Compute Provisioner (deploy) |
| `SUBJECT_COMPUTE_DESTROY` | `compute.destroy` | Subject hacia el Compute Provisioner (destroy) |
| `SUBJECT_NETWORK_DEPLOY` | `network.deploy` | Subject hacia el Network Orchestrator (deploy) |
| `SUBJECT_NETWORK_DESTROY` | `network.destroy` | Subject hacia el Network Orchestrator (destroy) |
| `JS_STREAM_NAME` | `SLICES` | Nombre del stream JetStream (captura `slice.*`) |
| `JS_KV_BUCKET` | `slice-state` | Bucket KV para persistir estado de operaciones |
| `COMPUTE_TIMEOUT` | `300` | Segundos máximos para esperar al Compute Provisioner |
| `NETWORK_TIMEOUT` | `60` | Segundos máximos para esperar al Network Orchestrator |
| `LOG_LEVEL` | `INFO` | Nivel de logging |
| `HEALTH_PORT` | `8080` | Puerto del healthcheck HTTP |

---

## Despliegue con Docker

Este módulo incluye el servidor NATS. Levantarlo aquí lo hace disponible para
el Compute Provisioner y el Network Orchestrator automáticamente.

```bash
# 1. Levantar (incluye NATS)
docker compose up -d

# 2. Verificar Queue Manager
curl http://localhost:8082/health
# → {"status": "ok", "nats": true}

# 3. Verificar NATS
curl http://localhost:8222/healthz
# → {"status": "ok"}

# 4. Logs
docker compose logs -f queue-manager
```

---

## Puertos expuestos

| Puerto | Servicio | Descripción |
|--------|----------|-------------|
| `4222` | NATS | Clientes (todos los módulos se conectan aquí) |
| `8222` | NATS | Monitoring HTTP (`/healthz`, `/varz`, `/connz`) |
| `8082` | Queue Manager | Healthcheck HTTP |

---

## Prueba manual sin Slice Manager

Con la NATS CLI instalada, publicar mensajes directamente al stream:

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
    "disk_gb": 10,
    "image_path": "/images/cirros-0.5.1-x86_64-disk.img",
    "vnc_port": 5901,
    "vnc_display": 1,
    "tap_interfaces": []
  }],
  "links": []
}'

# Publicar un destroy
nats pub slice.destroy '{
  "slice_id": "slice-001",
  "request_id": "req-002",
  "vms": [],
  "links": []
}'
```

---

## Nota importante sobre el stream SLICES

El stream `SLICES` solo captura `slice.*`. Los subjects `compute.*` y `network.*`
usan core NATS request/reply directamente y **no pasan por el stream**.

Si el stream fue creado previamente con sujetos incorrectos, hay que eliminarlo
antes de levantar el servicio para que el Queue Manager lo recree correctamente:

```bash
nats stream delete SLICES
```
