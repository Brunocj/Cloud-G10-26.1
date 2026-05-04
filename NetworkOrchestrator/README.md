# Network Orchestrator — PUCP Cloud Orchestrator

Microservicio encargado de la configuración de red Layer 2 del sistema de slices.
Recibe órdenes del Queue Manager via NATS, configura VLANs y puertos OVS en los
workers via SSH, y responde el resultado de vuelta al Queue Manager.

---

## Responsabilidades

- Conectar interfaces TAP (ya creadas por el Compute Provisioner) al bridge OVS (`br-int`).
- Asignar la VLAN de aislamiento a cada TAP para garantizar separación entre slices.
- Aplicar reglas de firewall básicas por TAP usando iptables (security groups).
- Limpiar puertos OVS durante la destrucción de un slice.
- Procesar múltiples workers en paralelo para reducir el tiempo de despliegue.
- Responder el resultado al Queue Manager via NATS reply.

**No** crea las interfaces TAP — eso es responsabilidad del Compute Provisioner.  
**No** crea el bridge OVS (`br-int`) — ese bridge ya existe en el worker antes del deploy.  
**No** asigna VLANs — esas vienen pre-calculadas por el Slice Manager y viajan en el mensaje.  
**No** decide en qué worker va cada VM — eso es responsabilidad del VM Placement.

---

## Arquitectura del módulo

```
main.py
 ├── api/health.py              → GET /health (healthcheck HTTP, puerto 8084)
 ├── core/
 │    ├── config.py             → Variables de entorno (Settings)
 │    └── logging_config.py    → Configuración de logging estructurado
 ├── models/
 │    └── schemas.py            → Contratos de entrada/salida (Pydantic)
 └── services/
      ├── handlers.py           → Handlers NATS: validan JSON y disparan provisioner
      ├── provisioner.py        → Orquestación: agrupa endpoints por worker, paraleliza
      ├── network_executor.py   → Comandos OVS e iptables sobre el worker
      ├── ssh_client.py         → Wrapper SSH con llave PEM en memoria (Paramiko)
      └── queue_client.py       → Cliente NATS (subscribe, reply)
```

---

## Flujo de mensajes

```
Queue Manager
    │
    │  NATS request → network.deploy
    │  { slice_id, request_id, links: [{connection_id, vlan_id,
    │    vm1_worker_ip, vm1_tap, vm1_ssh_user, vm1_ssh_private_key, vm1_security_rules,
    │    vm2_worker_ip, vm2_tap, vm2_ssh_user, vm2_ssh_private_key, vm2_security_rules}] }
    ▼
Network Orchestrator
    │
    ├─ Agrupa endpoints por worker (un SSH por worker, no por enlace)
    ├─ Lanza un thread por worker (paralelo)
    │
    │  Por cada endpoint en el worker:
    ├─ SSH → workerN: ovs-vsctl --may-exist add-br br-int
    ├─ SSH → workerN: ovs-vsctl --may-exist add-port br-int {tap}
    ├─ SSH → workerN: ovs-vsctl set port {tap} tag={vlan_id}
    └─ SSH → workerN: iptables -I FORWARD ... (por cada security rule)
    │
    │  NATS reply → Queue Manager
    │  { slice_id, request_id, status, links_ok: [...], links_failed: [...] }
    ▼
Queue Manager
```

Para destroy, el flujo es análogo usando `network.destroy`:

```
Network Orchestrator (destroy)
    │
    ├─ Si el payload incluye 'links': agrupa por worker y limpia cada TAP
    ├─ SSH → workerN: ovs-vsctl --if-exists del-port br-int {tap}
    │
    │  Si el payload no incluye 'links': responde success directamente
    │  (la limpieza de TAPs queda delegada al destroy de las VMs)
    │
    │  NATS reply → Queue Manager
    ▼
Queue Manager
```

---

## Formato de mensajes

### Entrada: network.deploy

Las VLANs y las TAP interfaces **vienen pre-calculadas por el Slice Manager**.
El Network Orchestrator las aplica directamente sin modificarlas.

```json
{
  "slice_id": "slice-abc123",
  "request_id": "req-xyz789",
  "links": [
    {
      "connection_id": "conn-001",
      "vlan_id": 100,
      "vm1_id": "vm-1",
      "vm1_worker_ip": "10.0.10.2",
      "vm1_tap": "tap-vm1-0",
      "vm1_ssh_user": "ubuntu",
      "vm1_ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "vm1_security_rules": [
        { "allow_port": 22, "protocol": "tcp" }
      ],
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

> Un enlace con ambas VMs en el mismo worker genera dos entradas en ese worker
> (una por TAP), pero solo abre una conexión SSH.

### Entrada: network.destroy

```json
{
  "slice_id": "slice-abc123",
  "request_id": "req-xyz789",
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

> Si `links` viene vacío o ausente, el módulo responde `success` directamente
> y delega la limpieza de TAPs al destroy del Compute Provisioner.

### Salida: respuesta deploy (éxito)

```json
{
  "slice_id": "slice-abc123",
  "request_id": "req-xyz789",
  "status": "success",
  "links_ok": [
    { "connection_id": "conn-001", "error": null }
  ],
  "links_failed": []
}
```

### Valores de `status`

| Valor | Significado |
|-------|-------------|
| `success` | Todos los enlaces configurados correctamente |
| `error` | Ningún enlace pudo configurarse |
| `partial` | Algunos enlaces OK, otros fallaron |

---

## Modelo de aislamiento de red

Cada enlace lógico de la topología recibe una VLAN única asignada por el Slice Manager.
Esa VLAN es la que garantiza el aislamiento entre slices distintos en el mismo bridge OVS.

```
Worker A                          Worker B
┌─────────────────┐               ┌─────────────────┐
│  VM-1           │               │  VM-2           │
│  └─ tap-vm1-0   │               │  └─ tap-vm2-0   │
│       tag=100   │               │       tag=100   │
│  br-int (OVS)   │◄─── VXLAN ───►│  br-int (OVS)   │
└─────────────────┘               └─────────────────┘
```

Ambos TAPs comparten el `tag=100` — VLAN 100 es el "cable virtual" entre vm-1 y vm-2.
Cualquier otra VM en otro slice que use VLAN 101 no verá ese tráfico.

---

## Optimización: una SSH por worker

El provisioner agrupa todos los endpoints de un mismo worker antes de conectar.
Esto significa que si un slice tiene 5 VMs en el mismo worker (5 TAPs a configurar),
se abre **una sola conexión SSH** y se ejecutan los 5 comandos secuencialmente.
Los distintos workers se procesan en **paralelo** mediante `ThreadPoolExecutor`.

---

## Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `NATS_URL` | `nats://nats:4222` | URL del servidor NATS |
| `QUEUE_DEPLOY` | `network.deploy` | Subject de entrada para deploy |
| `QUEUE_DESTROY` | `network.destroy` | Subject de entrada para destroy |
| `SSH_TIMEOUT` | `30` | Timeout de conexión SSH (segundos) |
| `SSH_MAX_RETRIES` | `3` | Reintentos ante fallo SSH |
| `SSH_RETRY_DELAY` | `5` | Segundos entre reintentos |
| `MAX_CONCURRENT_WORKERS` | `10` | Workers configurados en paralelo |
| `LOG_LEVEL` | `INFO` | Nivel de logging |
| `HEALTH_PORT` | `8084` | Puerto del healthcheck HTTP |

---

## Prerequisitos en los workers

Antes de desplegar, cada worker debe tener:

1. **Open vSwitch instalado** (el bridge `br-int` puede no existir — el módulo lo crea si falta)
   ```bash
   sudo apt install -y openvswitch-switch
   ```

2. **iptables disponible** para security groups
   ```bash
   sudo apt install -y iptables
   ```

3. **Acceso SSH con llave PEM** — la llave privada viene en cada mensaje.
   La llave pública debe estar en `~/.ssh/authorized_keys` del worker.

4. **sudo sin contraseña** para el usuario SSH
   ```bash
   echo "ubuntu ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/ubuntu-nopasswd
   ```

5. **Las interfaces TAP ya creadas** por el Compute Provisioner antes de que
   el Network Orchestrator intente conectarlas al bridge OVS.

---

## Despliegue con Docker

```bash
# 1. Levantar
docker compose up -d

# 2. Verificar
curl http://localhost:8084/health
# → {"status": "ok", "nats": true}

# 3. Logs
docker compose logs -f network-orchestrator
```

El healthcheck reporta `"status": "degraded"` si la conexión con NATS se pierde,
y devuelve HTTP 503 en ese caso.

> **Nota:** Este módulo se conecta al mismo NATS que el Queue Manager y el
> Compute Provisioner. Asegurarse de que `pucp_cloud_net` esté creada antes
> de levantar el contenedor.
