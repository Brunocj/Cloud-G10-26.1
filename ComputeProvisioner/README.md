# Compute Provisioner — PUCP Cloud Orchestrator

Microservicio encargado del aprovisionamiento computacional del sistema de slices.
Recibe órdenes del Queue Manager via NATS, crea y destruye VMs en los workers
via SSH+QEMU/KVM, y responde el resultado de vuelta al Queue Manager.

---

## Responsabilidades

- Crear discos QCOW2 con thin provisioning (backing file = imagen base) en cada worker.
- Crear interfaces TAP en el worker y conectarlas al bridge OVS (`br-int`).
- Lanzar procesos QEMU/KVM con las interfaces TAP y MACs pre-asignadas.
- Destruir VMs: matar proceso, limpiar TAPs del OVS y del kernel, eliminar disco.
- Reintentar ante fallos transitorios.
- Responder el resultado al Queue Manager via NATS reply.

**No** decide en qué worker va cada VM — eso es responsabilidad del VM Placement.  
**No** asigna MACs ni nombres de TAP — esos vienen calculados por el Slice Manager y viajan en el mensaje.  
**No** asigna el puerto VNC ni la ruta de imagen — ambos vienen pre-calculados en el mensaje desde el Slice Manager.  
**No** crea el bridge OVS (`br-int`) — ese bridge ya existe en el worker antes del deploy.

---

## Arquitectura del módulo

```
main.py
 ├── api/health.py                      → GET /health (healthcheck HTTP, puerto interno 8080 / externo 8081)
 ├── core/
 │    ├── config.py                     → Variables de entorno (Settings)
 │    ├── worker.py                     → Loop async: suscribe handlers NATS
 │    └── logging_config.py            → Configuración de logging estructurado
 ├── models/
 │    └── schemas.py                    → Contratos de entrada/salida (Pydantic)
 ├── services/
 │    ├── provisioner.py                → Orquestación deploy/destroy Linux Cluster (QEMU/KVM)
 │    ├── qemu_executor.py              → Comandos QEMU/KVM + TAP/OVS sobre el worker
 │    ├── openstack_compute_executor.py → Gestión de instancias via openstacksdk (OpenStack)
 │    ├── ssh_client.py                 → Wrapper SSH con llave PEM en memoria (Paramiko)
 │    ├── vnc_port_manager.py           → Consulta puertos VNC en uso via SSH (utilidad)
 │    └── queue_client.py               → Cliente NATS (subscribe, reply, KV)
 └── utils/
      └── image_resolver.py            → Cálculo de rutas de disco VM
```

### Strategy Pattern por Availability Zone

Los handlers NATS bifurcan la ejecución según `availability_zone_id` del mensaje:

```
availability_zone_id == 2  →  OpenStackComputeExecutor  (openstacksdk: Nova + Glance)
availability_zone_id != 2  →  provisioner.py            (SSH + QEMU/KVM)
```

Para OpenStack (`OpenStackComputeExecutor`):
- Resuelve el UUID de imagen en Glance a partir del `image_path` del mensaje.
- Crea la instancia Nova con el flavor apropiado (o el más cercano disponible).
- Para el destroy: termina la instancia Nova y libera la floating IP si la tenía.
- `get_console_token()`: pide a Nova un token noVNC fresco (corta duración, un solo uso)
  que el Slice Manager almacena en `deployed_vms.vnc_url` para que la Web App abra la consola.

---

## Flujo de mensajes

### Deploy

```
Queue Manager
    │
    │  NATS request → compute.deploy
    │  { slice_id, request_id, vms: [{vm_id, worker_ip, ssh_user,
    │    ssh_private_key, vcpus, ram_mb, disk_gb, image_path, vnc_port,
    │    internet_access, external_ip, internal_ip,
    │    tap_interfaces: [{tap_name, mac}, ...], priority}] }
    ▼
Compute Provisioner
    │
    ├─ SSH → workerN: qemu-img create -f qcow2 -b {image_path} -F qcow2 {disk}.qcow2 {disk_gb * 1024}M
    ├─ SSH → workerN: ip tuntap add dev {tap} mode tap          (por cada TAP)
    ├─ SSH → workerN: ovs-vsctl add-port br-int {tap}           (por cada TAP)
    ├─ SSH → workerN: ip link set {tap} up                      (por cada TAP)
    ├─ SSH → workerN: nice -n {priority-20} qemu-system-x86_64
    │                   -netdev tap,id=net0,ifname={tap},script=no,downscript=no
    │                   -device virtio-net-pci,netdev=net0,mac={mac}
    │                   -vnc 0.0.0.0:{vnc_port - 5900},websocket=on
    │                   -daemonize
    └─ SSH → workerN: pgrep -f "name {vm_id}-{slice_id}" → obtener PID
    │
    │  Persiste estado en NATS KV: compute-vms:{slice_id}
    │
    │  NATS reply → Queue Manager
    │  { slice_id, request_id, status, vms: [{vm_id, worker_ip, pid, vnc_port}] }
    ▼
Queue Manager
```

### Destroy

```
Compute Provisioner (destroy)
    │
    ├─ Lee VMs del payload (prioridad) o del KV como fallback
    ├─ SSH → workerN: pkill -f "name {vm_id}-{slice_id}"
    ├─ SSH → workerN: ovs-vsctl del-port br-int {tap}           (por cada TAP)
    ├─ SSH → workerN: ip tuntap del dev {tap} mode tap          (por cada TAP)
    └─ SSH → workerN: rm -f /vms/{vm_id}-{slice_id}.qcow2
    │
    │  Elimina estado del KV
    │  NATS reply → Queue Manager
    ▼
Queue Manager
```

---

## Formato de mensajes

### Entrada: compute.deploy

El puerto VNC, la ruta de imagen, las MACs y los nombres de TAP **vienen todos
pre-calculados por el Slice Manager**. El Compute Provisioner los aplica directamente.

```json
{
  "slice_id": "42",
  "request_id": "req-xyz789",
  "vms": [
    {
      "vm_id": "vm-1",
      "worker_ip": "10.0.10.2",
      "ssh_user": "ubuntu",
      "ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "vcpus": 1,
      "ram_mb": 512,
      "disk_gb": 10,
      "image_path": "/images/cirros-0.5.1-x86_64-disk.img",
      "vnc_port": 5901,
      "internet_access": 1,
      "external_ip": "192.168.100.50",
      "internal_ip": "10.0.42.10",
      "tap_interfaces": [
        { "tap_name": "t-042-n214-mgmt", "mac": "52:54:00:A3:C7:00" },
        { "tap_name": "t-042-n214-n215", "mac": "52:54:00:A3:C7:01" }
      ],
      "priority": 20
    }
  ]
}
```

> `priority` va de 0 (máxima prioridad de CPU) a 39 (mínima). Se mapea a `nice`
> de Linux como `nice = priority - 20`, es decir: 0 → nice -20, 20 → nice 0, 39 → nice 19.

> Una VM sin interfaces de red puede omitir `tap_interfaces` o enviarlo vacío.
> En ese caso QEMU arranca con `-netdev user` (modo NAT, útil para pruebas).

### Entrada: compute.destroy

```json
{
  "slice_id": "42",
  "request_id": "req-xyz789",
  "vms": [
    {
      "vm_id": "vm-1",
      "worker_ip": "10.0.10.2",
      "ssh_user": "ubuntu",
      "ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "tap_interfaces": [
        { "tap_name": "t-042-n214-mgmt", "mac": "52:54:00:A3:C7:00" },
        { "tap_name": "t-042-n214-n215", "mac": "52:54:00:A3:C7:01" }
      ]
    }
  ]
}
```

> Si `vms` viene vacío o ausente, el módulo hace fallback al KV store de NATS
> para recuperar el estado guardado durante el deploy.

### Salida: respuesta deploy (éxito)

```json
{
  "slice_id": "42",
  "request_id": "req-xyz789",
  "status": "success",
  "vms": [
    { "vm_id": "vm-1", "worker_ip": "10.0.10.2", "pid": 14823, "vnc_port": 5901 }
  ],
  "failed_vms": []
}
```

### Valores de `status`

| Valor | Significado |
|-------|-------------|
| `success` | Todas las VMs operaron correctamente |
| `error` | Ninguna VM pudo completar la operación |
| `partial` | Algunas VMs OK, otras fallaron (solo en deploy) |

---

## Asignación de MACs

Las MACs son calculadas por el **Slice Manager** al momento de definir la topología
del slice, antes de que el mensaje llegue al Queue Manager. El Compute Provisioner
las recibe ya resueltas en `tap_interfaces` y las aplica directamente al comando QEMU.

### Esquema

```
52:54:00 : XX : YY : ZZ

  52:54:00  → prefijo QEMU estándar (localmente administrado, unicast)
  XX:YY     → 2 bytes derivados del hash SHA-256 del slice_id
  ZZ        → índice secuencial global dentro del slice (0–255)
```

### Ejemplo

Para `slice_id=42` con 1 VM de 2 interfaces:

| VM | Interfaz | MAC |
|----|----------|-----|
| vm-1 | t-042-n214-mgmt | `52:54:00:A3:C7:00` |
| vm-1 | t-042-n214-n215 | `52:54:00:A3:C7:01` |

---

## Gestión de TAP interfaces

Las interfaces TAP actúan como punto de conexión entre las VMs y el bridge OVS
(`br-int`) que ya existe en cada worker.

```
VM (QEMU)
  └─ virtio-net-pci (mac=52:54:00:...)
       └─ t-042-vmX-Y   ← creada por este módulo
            └─ br-int (OVS)  ← preexistente en el worker
                 └─ ens4 → red de transporte
```

El bridge OVS es siempre `br-int` en todos los workers (configurable con
`OVS_BRIDGE` en `.env`). Las VLANs son asignadas luego por el Network Orchestrator.

---

## Persistencia de estado (NATS KV)

Después de un deploy exitoso, el módulo guarda el estado de las VMs en NATS KV
bajo la clave `compute-vms:{slice_id}`. Esto permite al destroy recuperar las
credenciales SSH y las TAP interfaces aunque el Slice Manager no las reenvíe.

El destroy siempre intenta leer las VMs del payload primero; solo recurre al KV
si el campo `vms` llega vacío (modo fallback por retrocompatibilidad).

El estado en KV se elimina automáticamente tras cada destroy, o expira a las
**3600 segundos** si no se ejecuta destroy explícito.

---

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `NATS_URL` | `nats://nats:4222` | URL del servidor NATS |
| `NATS_KV_BUCKET` | `compute-state` | Bucket KV para persistir VMs desplegadas |
| `QUEUE_DEPLOY` | `compute.deploy` | Subject de entrada para deploy |
| `QUEUE_DESTROY` | `compute.destroy` | Subject de entrada para destroy |
| `OVS_BRIDGE` | `br-int` | Nombre del bridge OVS en los workers |
| `SSH_TIMEOUT` | `30` | Timeout de conexión SSH (segundos) |
| `SSH_MAX_RETRIES` | `3` | Reintentos por VM ante fallo |
| `SSH_RETRY_DELAY` | `5` | Segundos entre reintentos |
| `IMAGES_BASE_DIR` | `/images` | Directorio de imágenes base en workers |
| `VMS_BASE_DIR` | `/vms` | Directorio de discos VM en workers |
| `MAX_CONCURRENT_WORKERS` | `10` | Workers procesados en paralelo |
| `LOG_LEVEL` | `INFO` | Nivel de logging |
| `HEALTH_PORT` | `8080` | Puerto interno del healthcheck HTTP (mapeado al 8081 en Docker) |
| `OS_AUTH_URL` | — | Endpoint Keystone de OpenStack |
| `OS_USERNAME` | `admin` | Usuario OpenStack |
| `OS_PASSWORD` | — | Contraseña OpenStack |
| `OS_PROJECT_NAME` | `admin` | Proyecto OpenStack |
| `OS_USER_DOMAIN_NAME` | `Default` | Dominio de usuario OpenStack |
| `OS_PROJECT_DOMAIN_NAME` | `Default` | Dominio de proyecto OpenStack |

---

## Prerequisitos en los workers

Antes de desplegar, cada worker debe tener:

1. **QEMU/KVM instalado**
   ```bash
   sudo apt install -y qemu-system-x86 qemu-utils
   ```

2. **Open vSwitch instalado** y el bridge `br-int` creado
   ```bash
   sudo apt install -y openvswitch-switch
   sudo ovs-vsctl add-br br-int
   sudo ip link set br-int up
   ```

3. **Acceso SSH con llave PEM** — la llave privada viene en cada mensaje.
   La llave pública debe estar en `~/.ssh/authorized_keys` del worker.

4. **sudo sin contraseña** para el usuario SSH
   ```bash
   echo "ubuntu ALL=(ALL) NOPASSWD:ALL" | sudo tee /etc/sudoers.d/ubuntu-nopasswd
   ```

5. **Directorios creados**
   ```bash
   sudo mkdir -p /images /vms
   ```

6. **Imágenes base presentes** en `/images/`
   ```bash
   sudo wget -P /images http://download.cirros-cloud.net/0.5.1/cirros-0.5.1-x86_64-disk.img
   ```

---

## Despliegue con Docker

```bash
# 1. Levantar
docker compose up -d

# 2. Verificar (puerto externo 8081)
curl http://localhost:8081/health
# → {"status": "ok", "nats": true}

# 3. Logs
docker compose logs -f compute-provisioner
```

El healthcheck reporta `"status": "degraded"` si la conexión con NATS se pierde,
y devuelve HTTP 503 en ese caso.

> **Nota:** Si el Queue Manager ya tiene NATS corriendo, apunta `NATS_URL` al
> mismo NATS compartido y levanta solo el servicio `compute-provisioner`.

---

## Identificación de procesos en los workers

Cada proceso QEMU se nombra con el patrón `{vm_id}-{slice_id}`:

```bash
pgrep -f "name vm-1-42"
# → 14823

ps aux | grep "vm-1-42"
```

---

## Nota sobre imágenes (evolución futura)

La ruta de imagen viene pre-calculada en el mensaje (`image_path`), calculada
por el Slice Manager. La función `app/utils/image_resolver.py:get_image_path()`
sigue disponible como utilidad, pero el punto de extensión principal para migrar
a distribución dinámica desde una BD es el Slice Manager, no este módulo.

`get_vm_disk_path()` es el único lugar donde se calcula la ruta del disco thin —
si se cambia el esquema de nombres, solo se modifica ahí.
