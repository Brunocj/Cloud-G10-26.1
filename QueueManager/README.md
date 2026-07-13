# Queue Manager / Workflow Orchestrator — PUCP Cloud Orchestrator

Orquestador central del sistema (a.k.a. **Workflow Orchestrator**). Recibe
solicitudes del Slice Manager y ejecuta el ciclo de vida del slice como un
**patrón SAGA** de transacciones distribuidas, coordinando VM Placement, Network
Orchestrator y Compute Provisioner. Aloja el servidor NATS embebido.

Es **agnóstico a la plataforma**: propaga `availability_zone_id` en todos los
payloads sin interpretarlo — cada módulo ejecutor aplica su Strategy (Linux u OpenStack).

## SAGA de despliegue (orden ESTRICTO)

```
Paso 1 — PLACEMENT : slice.placement.process → VM Placement  (selected_host por VM)
Paso 2 — NETWORK   : network.deploy          → Network Orch.  (crea red, devuelve port_map)
Paso 3 — COMPUTE   : compute.deploy          → Compute Prov.  (crea las VMs)
Paso 4 — STATE     : slice.state.update      → Slice Manager  (marca ACTIVE)
```

> **La red va ANTES que el cómputo** (inversión respecto a la Fase 1): en OpenStack
> Nova exige los puertos Neutron ya creados, así que Network devuelve el `port_map`
> antes de que Compute cree las instancias. En Linux el orden es el mismo por consistencia.

**Rollback (compensación en orden inverso):** si falla COMPUTE, se llama a
`network.destroy` para limpiar la red creada; si falla NETWORK/PLACEMENT no hay
infraestructura que compensar. El Slice Manager mantiene una segunda capa de rollback.

## Responsabilidades

- Recibir `slice.deploy` / `slice.destroy` del Slice Manager (JetStream, con ACK).
- Ejecutar la SAGA de 4 pasos (destroy en orden inverso: Compute → Network).
- Propagar `availability_zone_id`, `host_map`, `mgmt_vlan` y `compute_ssh_map` a los módulos.
- Balancear las solicitudes de gestión hacia las réplicas del Slice Manager (**queue groups**).
- Persistir el estado de cada operación en NATS JetStream KV (para rollback selectivo).
- Notificar el resultado final (`slice.result`) y alojar el NATS embebido.

**No** ejecuta comandos en workers (Compute) · **No** configura red (Network) ·
**No** decide el worker de cada VM (VM Placement).

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
Workflow Orchestrator
    │
    ├─ ACK inmediato al stream
    ├─ Persiste estado en NATS KV
    │
    ├─ Paso 1: request → slice.placement.process  (VM Placement)
    │           → host_map (vm_id → selected_host). Si falla: notifica error (sin limpiar)
    │
    ├─ Paso 2: request → network.deploy  (timeout: 60s)
    │           envía host_map + mgmt_vlan + compute_ssh_map + links + vms
    │           → port_map. Si falla: notifica error
    │
    ├─ Paso 3: request → compute.deploy  (timeout: 300s)
    │           envía selected_host + network_ports por VM
    │           si falla → ROLLBACK: network.destroy, luego notifica error
    │
    ├─ Paso 4: publish → slice.state.update (Slice Manager marca ACTIVE)
    ├─ Elimina estado del KV
    └─ publish → slice.result
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
    ├─ Paso 0: core NATS request → compute.destroy  (timeout: 300s)
    │           termina las VMs (mata QEMU en Linux / delete_server en Nova)
    │
    ├─ Paso 1: core NATS request → network.destroy  (timeout: 60s)
    │           limpia red (puertos OVS/gateway en Linux; Neutron en OpenStack)
    │           si no responde: warning y continúa (no bloquea)
    │
    ├─ Elimina estado del KV
    └─ core NATS publish → slice.result
    │
    ▼
Slice Manager
```

> El destroy va en orden inverso al deploy: primero se terminan las VMs
> (Compute) y luego se limpia la red (Network).

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
      "internet_access": 1,
      "external_ip": "192.168.100.50",
      "internal_ip": "10.0.42.10",
      "tap_interfaces": [
        { "tap_name": "t-042-n214-m",    "mac": "52:54:00:A3:C7:00" },
        { "tap_name": "t-042-n214-n215", "mac": "52:54:00:A3:C7:01" }
      ],
      "priority": 20
    }
  ],
  "links": [
    {
      "connection_id": "conn-001",
      "vlan_id": 100,
      "vm1_id": "vm-1",
      "vm1_worker_ip": "10.0.10.2",
      "vm1_tap": "t-042-n214-n215",
      "vm1_ssh_user": "ubuntu",
      "vm1_ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "vm1_security_rules": [{ "allow_port": 22, "protocol": "tcp" }],
      "vm2_id": "vm-2",
      "vm2_worker_ip": "10.0.10.3",
      "vm2_tap": "t-042-n215-n214",
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
      "internet_access": 1,
      "external_ip": "192.168.100.50",
      "internal_ip": "10.0.42.10",
      "tap_interfaces": [
        { "tap_name": "t-042-n214-m",    "mac": "52:54:00:A3:C7:00" },
        { "tap_name": "t-042-n214-n215", "mac": "52:54:00:A3:C7:01" }
      ]
    }
  ],
  "links": [
    {
      "connection_id": "conn-001",
      "vlan_id": 100,
      "vm1_worker_ip": "10.0.10.2",
      "vm1_tap": "t-042-n214-n215",
      "vm1_ssh_user": "ubuntu",
      "vm1_ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "vm2_worker_ip": "10.0.10.3",
      "vm2_tap": "t-042-n215-n214",
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
| `SUBJECT_PLACEMENT` | `slice.placement.process` | Subject hacia el VM Placement (Paso 1) |
| `PLACEMENT_TIMEOUT` | `30` | Segundos máximos para esperar al VM Placement |
| `SUBJECT_COMPUTE_DEPLOY` | `compute.deploy` | Subject hacia el Compute Provisioner (deploy) |
| `SUBJECT_COMPUTE_DESTROY` | `compute.destroy` | Subject hacia el Compute Provisioner (destroy) |
| `SUBJECT_COMPUTE_RESULT` | `compute.result` | Subject de resultado del Compute Provisioner |
| `SUBJECT_NETWORK_DEPLOY` | `network.deploy` | Subject hacia el Network Orchestrator (deploy) |
| `SUBJECT_NETWORK_DESTROY` | `network.destroy` | Subject hacia el Network Orchestrator (destroy) |
| `SUBJECT_NETWORK_RESULT` | `network.result` | Subject de resultado del Network Orchestrator |
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
