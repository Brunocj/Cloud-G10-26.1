# Network Orchestrator — PUCP Cloud Orchestrator

Microservicio encargado de la configuración de red del sistema de slices.
Recibe órdenes del Workflow Orchestrator (Queue Manager) via NATS y aprovisiona
la red en la infraestructura destino, respondiendo el resultado de vuelta.

Es **agnóstico a la zona** vía patrón **Strategy**: según `availability_zone_id`
del mensaje activa su ejecutor de **Linux Cluster** (OVS/SSH) o de **OpenStack**
(Neutron/openstacksdk), sin que el resto del sistema lo sepa.

---

## Estrategia por zona

| | **Linux Cluster** (`network_executor.py`) | **OpenStack** (`openstack_network_executor.py`) |
|---|---|---|
| Backend | OVS + iptables por SSH a los workers | API de Neutron (openstacksdk) |
| L2 | tag VLAN por enlace en `br-int`; trunk inter-worker por `ens4` | red/subred/puerto Neutron tipo **provider `vlan`** por enlace |
| Gestión | `mgmt_vlan` (VLAN de gestión del slice) + gateway/NAT/DHCP | `net-slice` + router + Floating IPs |
| Seguridad | iptables por TAP (security rules del usuario) | Security Groups de Neutron |
| Orden | Compute **antes** que Network (los TAP ya existen) | **Network antes que Compute** (Nova exige los puertos ya creados → devuelve `port_map`) |

### Dos capas de firewall

El sistema separa dos preguntas distintas, cada una con su propio punto de aplicación:

| | **Firewall Interno** (`firewall_rules`, por enlace) | **Reglas de Entrada desde Internet** (`ingress_rules`, por VM) |
|---|---|---|
| Responde | ¿Quién dentro del slice puede hablarle a esta VM? | ¿Qué puede entrar por la IP externa/VPN? |
| Se activa | Siempre que el enlace exista | Solo si la VM tiene `external_ip` asignada |
| Vacío = | Totalmente abierto (compat. legado) | **Deny-by-default** — nada entra salvo tráfico ya establecido |
| Linux | `iptables` sobre el TAP de cada enlace (`--physdev-out`) | `iptables` sobre el camino DNAT (`-d {internal_ip}`), NO sobre los TAPs |
| OpenStack | SG por VM aplicado a sus **puertos de enlace** | SG por VM aplicado **solo** al puerto de gestión (el de la Floating IP) |

> En Linux, el filtro de `ingress_rules` matchea **solo por IP destino**, sin
> restringir por interfaz de entrada. Se probó con `-i {EXTERNAL_INTERFACE}`
> (br-int) primero, pero en el cluster real ese match nunca hacía hit — con
> OVS, el paquete DNAT'eado no llega a `FORWARD` reportando `br-int` como
> interfaz de entrada (a diferencia de un bridge Linux normal), así que caía
> a la policy `ACCEPT` por defecto del chain y el filtro quedaba de adorno.

El checkbox "Acceso SSH externo (IP VPN)" del editor siembra automáticamente una
regla `TCP/22` en `ingress_rules` al asignar la IP — sin eso, el propio acceso
SSH que ese checkbox promete quedaría bloqueado por el deny-by-default.

## Responsabilidades

- **Linux:** conectar los TAP (creados por Compute) a `br-int`, aplicar el tag VLAN
  por enlace, mantener `ens4` como trunk inter-worker en `br-int`, configurar el
  gateway/NAT/DHCP del slice sobre la `mgmt_vlan`, y aplicar las reglas de firewall
  del usuario (iptables). Aislamiento extra: regla `FORWARD gw_+ → gw_+ DROP` para
  que gateways de slices distintos que comparten worker no se enruten entre sí.
- **OpenStack:** crear en Neutron la red/subred/puertos del slice, las redes de
  enlace (provider VLAN, con `segmentation_id` **forzado** al C-VID reservado por el
  Slice Manager cuando `OS_PROVIDER_PHYSICAL_NETWORK` está seteado), el router,
  las Floating IPs y los Security Groups; devuelve el `port_map` para el Compute.
- **Q-in-Q (802.1ad):** opcional (`QINQ_ENABLED`), **solo Linux** en el estado
  actual — interpone un `br-qinq` con `dot1q-tunnel` (S-VID por slice sobre los
  C-VIDs). En OpenStack quedó desactivado por conflictos con el uplink compartido.
- **Purga idempotente:** guardia pre-deploy y destroy borran por **ID** todos los
  recursos homónimos del slice, evitando duplicados de intentos fallidos.
- Procesar múltiples workers en **paralelo** y responder al Workflow Orchestrator.

**No** crea las interfaces TAP (Compute) · **No** decide el worker de cada VM
(VM Placement) · **No** asigna las VLANs/C-VIDs — vienen pre-calculadas por el
Slice Manager y viajan en el mensaje.

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
      ├── handlers.py                    → Handlers NATS: validan JSON y despachan por zona
      ├── provisioner.py                 → Orquestación Linux: agrupa endpoints por worker, paraleliza
      ├── network_executor.py            → Comandos OVS/iptables/Q-in-Q sobre el worker (Linux)
      ├── openstack_network_executor.py  → Aprovisionamiento Neutron (redes, puertos, router, SGs, FIPs, purga)
      ├── ssh_client.py                  → Wrapper SSH con llave PEM en memoria (Paramiko)
      └── queue_client.py                → Cliente NATS (subscribe, reply)
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

Además de `links`, el mensaje trae campos de nivel superior que el ejecutor usa
según la zona:

| Campo | Uso |
|---|---|
| `availability_zone_id` | 1=Linux, 2=OpenStack → selecciona el ejecutor (Strategy) |
| `host_map` | `vm_id → selected_host` (del VM Placement) — dónde va cada VM |
| `mgmt_vlan` | VLAN de gestión del slice (reservada por el Slice Manager) |
| `links[].vlan_id` / `links[].s_vlan_id` | C-VID por enlace / S-VID del slice (Q-in-Q) |
| `compute_ssh_map` | credenciales SSH por host de Nova (Q-in-Q OpenStack, si aplica) |
| `vms[]` | specs de red por VM (IP interna, acceso a internet, IP externa, `ingress_rules`) |

La respuesta de deploy incluye además el **`port_map`** (`vm_id → {provider_port_id,
external_ip, link_ports}`) en OpenStack, que el Compute Provisioner consume para
crear las instancias con sus puertos Neutron.

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

**El tráfico inter-worker NO usa túneles** (ni VXLAN ni GRE): viaja etiquetado con
802.1Q sobre el trunk físico `ens4`, que se mantiene colgado de `br-int`. Ese es el
requisito de "VLANs, no self-service" (R5).

```
Worker A                                   Worker B
┌────────────────────┐                     ┌────────────────────┐
│  VM-1              │                      │  VM-2              │
│  └─ tap-vm1-0 tag=100                     │  └─ tap-vm2-0 tag=100
│  br-int (OVS) ── ens4 ─┤  trunk 802.1Q  ├─ ens4 ── br-int (OVS)│
└────────────────────┘   (VLAN 100 taggeada)  └────────────────────┘
```

Ambos TAPs comparten `tag=100` — VLAN 100 es el "cable virtual" entre vm-1 y vm-2.
Otra VM en otro slice con VLAN 101 no ve ese tráfico.

**Q-in-Q (Linux, opcional):** con `QINQ_ENABLED=true`, `ens4` se mueve a un
`br-qinq` que empuja un **S-VID por slice** (0x88a8) sobre los C-VIDs de sus
enlaces, permitiendo reutilizar C-VIDs entre slices. Desactivado por defecto.

**OpenStack:** el aislamiento lo da Neutron con redes **provider VLAN** (`vlan`,
no self-service/túnel). El `segmentation_id` se fuerza al C-VID de la tabla
`vlans` cuando el physnet está configurado, para que la BD coincida con el cable.

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
| `DATA_TRUNK_IFACE` | `ens4` | Interfaz trunk inter-worker que se cuelga de `br-int` (Linux) |
| `LOG_LEVEL` | `INFO` | Nivel de logging |
| `HEALTH_PORT` | `8084` | Puerto del healthcheck HTTP |
| `OS_AUTH_URL`, `OS_USERNAME`, `OS_PASSWORD`, `OS_PROJECT_NAME`, `OS_*_DOMAIN_NAME` | — | Credenciales OpenStack (Neutron) |
| `OS_PROVIDER_PHYSICAL_NETWORK` | — | physnet para forzar el `segmentation_id` de las redes provider VLAN |
| `OS_EXTERNAL_NETWORK_NAME` | — | red externa para Floating IPs |

> El Q-in-Q se activa con `QINQ_ENABLED` en el **Slice Manager** (es quien asigna
> el S-VID). Este módulo solo aplica el túnel si el mensaje trae `s_vlan_id`.

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
