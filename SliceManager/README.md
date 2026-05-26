# Slice Manager — PUCP Cloud Orchestrator

Director de orquestación del sistema. Recibe topologías del frontend (canvas),
las persiste en MySQL, coordina el VM Placement, enriquece el contrato de despliegue
y publica las órdenes al Queue Manager via NATS JetStream. También escucha los
resultados y ejecuta rollback automático ante fallos.

---

## Responsabilidades

- Exponer la API REST que consume el frontend (borradores, deploy, destroy).
- Persistir slices y VMs en MySQL (modelo relacional).
- **Calcular y persistir el peso de cada VM al guardar el borrador** (`peso` y `peso_actualizado`).
- **Construir el Servers' State desde BD** al momento del deploy: obtiene workers de la zona, aplica factor de overprovisioning y descuenta pesos de VMs activas.
- Invocar al VM Placement para obtener el mapa `vm → worker`.
- Enriquecer el contrato con: IPs SSH, llaves PEM, MACs, TAP names (incluido TAP de gestión), VLANs, VNC ports, rutas de imagen, IPs internas de la subred de gestión.
- Publicar `slice.deploy` y `slice.destroy` en NATS JetStream hacia el Queue Manager.
- Escuchar `slice.result` y actualizar el estado en MySQL.
- Ejecutar rollback automático (publish destroy) si el resultado es error.
- Gestionar el pool de IPs externas (tabla `ip_pool`): reservar al crear borrador, liberar al destruir o actualizar.

**No** ejecuta comandos en workers — eso es del Compute Provisioner.  
**No** configura red — eso es del Network Orchestrator.  
**No** decide el algoritmo de placement — eso es del VM Placement.  
**No** consulta Prometheus para el placement — el Servers' State se construye desde BD.

---

## Arquitectura del módulo

```
main.py                          → FastAPI app, lifespan, routers
app/
 ├── database.py                 → Engine MySQL + SessionLocal + get_db()
 ├── models.py                   → ORM SQLAlchemy (tablas MySQL)
 ├── schemas.py                  → Pydantic: DeployRequest, DraftSaveRequest
 ├── nats_producer.py            → Cliente NATS JetStream (publish deploy/destroy)
 ├── routers/
 │    ├── slice_router.py        → CRUD de borradores + endpoints utilitarios
 │    └── deploy_router.py       → POST deploy, DELETE destroy
 ├── repositories/
 │    └── slice_repo.py          → SliceRepository.save_draft()
 └── services/
      ├── placement_worker.py    → Worker async: Servers' State → placement → enriquecimiento → NATS
      ├── nats_listener.py       → Listener async: slice.result → actualiza MySQL
      ├── gc_scheduler.py        → Garbage collector de ISOs, discos y imágenes huérfanas
      └── telemetry.py           → Módulo de telemetría (reservado para Observabilidad)
```

---

## Modelo de pesos

Al guardar un borrador, cada VM persiste su peso calculado con la fórmula:

```
w = 3·vcpus + 5·ram_gb + 1·disk_gb
```

Con coeficientes derivados del cuello de botella del clúster (RAM es el recurso más escaso):

```
β (RAM) : α (vCPU) : γ (disco) = 5 : 3 : 1
```

Se persisten dos campos en la tabla `vms`:
- `peso`: peso nominal calculado sobre recursos solicitados. **No cambia.**
- `peso_actualizado`: arranca igual que `peso`. El módulo de Observabilidad lo irá corrigiendo con consumo real. **Es el que usa el Servers' State.**

---

## Servers' State y factor de overprovisioning

Al recibir una orden de deploy, el `placement_worker` construye el Servers' State desde BD:

```
F_OP = 1 / 0.65 ≈ 1.54          (factor de overprovisioning inicial)

capacidad_nominal_i = 3·cpu_i + 5·(ram_gb_i) + 1·disk_gb_i
C_i = capacidad_nominal_i × F_OP
D_i = C_i − Σ peso_actualizado(VMs ACTIVE en worker_i)
```

El factor `F_OP` expande la capacidad nominal para reflejar que en la práctica las VMs
consumen ~65% de lo que solicitan. Cuando el módulo de Observabilidad esté operativo,
este factor será dinámico.

Solo se incluyen workers de la zona solicitada (`availability_zone_id`) y se excluye
siempre el worker con `id=1` (headnode).

---

## Flujo completo de deploy

```
Frontend
    │ POST /api/v1/slices/{id}/deploy
    │ { availability_zone_id, ttl_hours, motivo }
    ▼
deploy_router.py
    ├─ Valida que el slice exista en MySQL
    ├─ Cambia status → PENDING_APPROVAL
    └─ Encola en placement_queue { slice_id, zone_id }
       Responde 202 inmediatamente
    ▼
placement_worker.py (background)
    │
    ├─ 1. Lee VMs del slice desde MySQL (tabla vms)
    ├─ 2. Construye Servers' State desde BD:
    │       - Consulta workers de la zona (excluyendo headnode id=1)
    │       - Calcula C_i = capacidad_nominal × F_OP
    │       - Resta SUM(peso_actualizado) de VMs ACTIVE por worker
    │       - Arma lista { worker_id, disponible }
    ├─ 3. POST http://vm-placement:8080/placement → mapa vm→worker
    │
    │  Si placement == SUCCESS:
    ├─ 4. Para cada VM: genera TAP de gestión (eth0)
    │       - tap_name: t-{slice[-3:]}-{vm[:4]}-m
    │       - mac: 52:54:XX:YY:{counter:02x}
    │
    ├─ 5. Para cada edge del slice_json:
    │       - Genera tap names de datos: t-{slice[-3:]}-{vm1[:4]}-{vm2[:4]}
    │       - Genera MACs: 52:54:XX:YY:{counter:02x} (XX:YY = SHA256 del slice_id)
    │       - Asigna VLAN libre aleatoria (100–4000, sin colisión con BD)
    │       - Persiste Vlan en MySQL (tabla vlans)
    │       - Lee llave PEM desde ./keys/workerN.pem
    │
    ├─ 6. Para cada VM:
    │       - Asigna VNC port libre por worker (5901–5999, sin colisión con BD)
    │       - Consulta image_path desde tabla images
    │       - Calcula IP interna: 10.{slice//256}.{slice%256}.{10+idx}
    │       - Inyecta internet_access, external_ip, internal_ip
    │       - Construye VMSpec completo
    │
    ├─ 7. Guarda deployed_vms y deployed_links en slice_json (MySQL) — ANTES de publicar
    ├─ 8. Publica slice.deploy en NATS JetStream → Queue Manager
    └─ 9. Cambia status → PROVISIONING (o FAILED si el publish falló)
    ▼
Queue Manager → Compute + Network → slice.result
    ▼
nats_listener.py (background)
    ├─ status == "success" → slice.status = ACTIVE, vm.state = ACTIVE
    └─ status != "success" → slice.status = FAILED
                              libera VLANs de MySQL
                              publica slice.destroy (rollback automático)
```

---

## Flujo de destroy

```
Frontend
    │ DELETE /api/v1/slices/{id}
    ▼
deploy_router.py
    ├─ Si status == DRAFT:
    │     Elimina VMs + Slice de MySQL → responde 200
    │
    └─ Si status != DRAFT:
        ├─ Lee deployed_vms y deployed_links de slice_json
        ├─ Publica slice.destroy en NATS JetStream (con VMs + links completos)
        ├─ Libera VLANs de MySQL (tabla vlans)
        ├─ Cambia status → TERMINATED
        └─ Cambia state de todas las VMs → TERMINATED
```

---

## API REST

| Método | Ruta | Descripción |
|--------|------|-------------|
| `GET` | `/api/v1/slices/` | Lista todos los slices del usuario actual |
| `POST` | `/api/v1/slices/draft` | Crea un nuevo borrador con topología del canvas |
| `PUT` | `/api/v1/slices/{id}/draft` | Actualiza un borrador existente |
| `POST` | `/api/v1/slices/{id}/deploy` | Inicia el despliegue de un borrador |
| `DELETE` | `/api/v1/slices/{id}` | Destruye un slice activo o elimina un borrador |
| `GET` | `/api/v1/slices/utils/images` | Lista imágenes disponibles |
| `GET` | `/api/v1/slices/utils/workers` | Lista workers registrados |
| `GET` | `/api/v1/slices/utils/available-ips` | Lista IPs del pool que no están en uso |
| `GET` | `/` | Healthcheck básico |

---

## Formato del payload al VM Placement

```json
{
  "slice_id": "42",
  "availability_zone": "1",
  "vms": [
    { "vm_id": "n214", "peso": 18.5 },
    { "vm_id": "n215", "peso": 5.25 }
  ],
  "workers": [
    { "worker_id": 2, "disponible": 36.92 },
    { "worker_id": 3, "disponible": 36.92 },
    { "worker_id": 4, "disponible": 36.92 }
  ]
}
```

---

## Formato del mensaje NATS `slice.deploy`

```json
{
  "slice_id": "42",
  "request_id": "req-a1b2c3d4",
  "vms": [
    {
      "vm_id": "n214",
      "worker_ip": "10.0.10.2",
      "ssh_user": "ubuntu",
      "ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "vcpus": 1,
      "ram_mb": 512.0,
      "disk_gb": 10.0,
      "image_path": "/mnt/cloud_images/cirros-0.5.1-x86_64-disk.img",
      "vnc_port": 5901,
      "vnc_display": 1,
      "internet_access": 1,
      "external_ip": "192.168.100.50",
      "internal_ip": "10.0.42.10",
      "vm_user": "cirros",
      "vm_password": "pucp2026",
      "tap_interfaces": [
        { "tap_name": "t-042-n214-m",    "mac": "52:54:00:A3:C7:00" },
        { "tap_name": "t-042-n214-n215", "mac": "52:54:00:A3:C7:01" }
      ]
    }
  ],
  "links": [
    {
      "connection_id": "n214-n215-100",
      "vlan_id": 100,
      "vm1_id": "n214",
      "vm1_worker_ip": "10.0.10.2",
      "vm1_tap": "t-042-n214-n215",
      "vm1_ssh_user": "ubuntu",
      "vm1_ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "vm2_id": "n215",
      "vm2_worker_ip": "10.0.10.3",
      "vm2_tap": "t-042-n215-n214",
      "vm2_ssh_user": "ubuntu",
      "vm2_ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"
    }
  ]
}
```

> El TAP `t-042-n214-m` (sufijo `-m`) es la interfaz de gestión (eth0).
> Se genera siempre, independientemente de si la VM tiene enlaces.

---

## Esquema de generación de MACs

```
52:54:00 : XX : YY : ZZ

  52:54:00  → prefijo QEMU estándar
  XX:YY     → primeros 4 chars del SHA-256 del slice_id
  ZZ        → contador secuencial global dentro del slice (0x00–0xFF)
             (incluye el TAP de gestión, que siempre es el primero)
```

---

## Esquema de IPs internas (subred de gestión)

```
subred = 10. (slice_id // 256) % 256 . slice_id % 256 .0/24
VMs    = 10.X.Y.10, 10.X.Y.11, 10.X.Y.12, ...

Ejemplos:
  slice_id=1  → 10.0.1.10, 10.0.1.11, ...
  slice_id=42 → 10.0.42.10, 10.0.42.11, ...
```

---

## Base de datos MySQL

Tablas principales utilizadas por este módulo:

| Tabla | Descripción |
|-------|-------------|
| `slices` | Estado y metadatos del slice, incluye `slice_json` (JSON) con la topología y `deployed_vms`/`deployed_links` post-deploy |
| `vms` | VMs con recursos, `peso`, `peso_actualizado`, worker asignado, vnc_port, external_ip, internet_access |
| `vlans` | VLANs asignadas por slice (se limpian al destroy o rollback) |
| `images` | Imágenes disponibles con `path` absoluto en el worker |
| `workers` | Workers registrados con IP, zona de disponibilidad, `cpu`, `ram` y `disk_gb` |
| `ip_pool` | Pool de IPs externas flotantes: `is_used`, `vm_id` (FK) |
| `availability_zones` | Zonas de disponibilidad — el deploy filtra workers por `id` |

### Campos de peso en la tabla `vms`

| Campo | Descripción |
|-------|-------------|
| `peso` | Peso nominal calculado al crear el borrador. No cambia. |
| `peso_actualizado` | Igual a `peso` al inicio. Actualizado por Observabilidad con consumo real. Usado para construir el Servers' State. |

### Campos de capacidad en la tabla `workers`

| Campo | Descripción |
|-------|-------------|
| `cpu` | Número de vCPUs del worker |
| `ram` | RAM total en MB |
| `disk_gb` | Disco total en GB — necesario para calcular la capacidad nominal en unidades de peso |

---

## Inventario de workers y llaves SSH

Las credenciales SSH se leen desde archivos `.pem` montados en el contenedor:

```
slice-manager/
└── keys/
    ├── worker2.pem   → worker_id=2, IP 10.0.10.2
    ├── worker3.pem   → worker_id=3, IP 10.0.10.3
    └── worker4.pem   → worker_id=4, IP 10.0.10.4
```

El worker con `id=1` es el headnode — corre los servicios de orquestación y **no recibe VMs**.
El mapeo `worker_id → IP` está hardcodeado en `placement_worker.py` (variable `server_inventory`).

### Generar y registrar llaves SSH

```bash
# En server1: generar un par por cada worker
ssh-keygen -t rsa -b 2048 -f ~/proyecto/SliceManager/keys/worker2.pem -N ""
ssh-keygen -t rsa -b 2048 -f ~/proyecto/SliceManager/keys/worker3.pem -N ""
ssh-keygen -t rsa -b 2048 -f ~/proyecto/SliceManager/keys/worker4.pem -N ""

# Registrar la llave pública en cada worker
ssh-copy-id -i ~/proyecto/SliceManager/keys/worker2.pem.pub ubuntu@10.0.10.2
ssh-copy-id -i ~/proyecto/SliceManager/keys/worker3.pem.pub ubuntu@10.0.10.3
ssh-copy-id -i ~/proyecto/SliceManager/keys/worker4.pem.pub ubuntu@10.0.10.4
```

---

## Variables de entorno

| Variable | Default | Descripción |
|---|---|---|
| `NATS_URL` | `nats://localhost:4222` | URL del servidor NATS |
| `VM_PLACEMENT_URL` | `http://vm-placement:8080/placement` | URL del VM Placement |
| `DB_HOST` | `mysql-db` | Host de MySQL |
| `DB_USER` | `root` | Usuario MySQL |
| `DB_PASSWORD` | `root` | Contraseña MySQL |
| `DB_NAME` | `cloud` | Base de datos MySQL |
| `GC_INTERVAL_HOURS` | `6` | Intervalo del Garbage Collector en horas |
| `IMAGES_DIR` | `/mnt/cloud_images` | Directorio NFS de imágenes |

---

## Despliegue con Docker

```bash
# 1. Colocar las llaves PEM en ./keys/
mkdir -p keys
# Generar o copiar worker2.pem, worker3.pem, worker4.pem

# 2. Levantar
docker compose up -d --build

# 3. Verificar
curl http://localhost:8000/
# → {"status": "✅ Slice Manager Online (Modo Híbrido)"}

# 4. Logs
docker compose logs -f slice-manager
```

> **Nota:** El Slice Manager se conecta a MySQL en `host.docker.internal`.
> Asegurarse de que MySQL esté corriendo antes de levantar.
