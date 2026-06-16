# Slice Manager — PUCP Cloud Orchestrator

Director de orquestación del sistema. Recibe topologías del frontend (canvas),
las persiste en MySQL, coordina el VM Placement, enriquece el contrato de despliegue
y publica las órdenes al Queue Manager via NATS JetStream. También escucha los
resultados y ejecuta rollback automático ante fallos.

---

## Responsabilidades

- Exponer la API REST que consume el frontend (borradores, deploy, destroy, imágenes).
- Persistir slices, VMs e imágenes en MySQL (modelo relacional).
- Aplicar control de acceso basado en roles (`X-User-Id` / `X-User-Role` inyectados por el API Gateway).
- Construir el Servers' State multidimensional (cpu/ram/disco) desde BD al momento del deploy.
- Invocar al VM Placement para obtener el mapa `vm → worker`.
- Enriquecer el contrato con: IPs SSH, llaves PEM, MACs, TAP names (incluido TAP de gestión), VLANs, VNC ports, rutas de imagen, IPs internas de la subred de gestión.
- Publicar `slice.deploy` y `slice.destroy` en NATS JetStream hacia el Queue Manager.
- Escuchar `slice.result` y actualizar el estado en MySQL.
- Ejecutar rollback automático (publish destroy) si el resultado es error.
- Gestionar el pool de IPs externas (tabla `ip_pool`): reservar al crear borrador, liberar al destruir.
- Gestionar imágenes: subir al NFS o a OpenStack Glance, listar catálogo híbrido, eliminar.

**No** ejecuta comandos en workers — eso es del Compute Provisioner.
**No** configura red — eso es del Network Orchestrator.
**No** decide el algoritmo de placement — eso es del VM Placement.

---

## Arquitectura del módulo

```
main.py                          → FastAPI app, lifespan, routers
app/
 ├── auth.py                     → Dependencias de identidad (X-User-Id / X-User-Role)
 ├── database.py                 → Engine MySQL + SessionLocal + get_db()
 ├── models.py                   → ORM SQLAlchemy (tablas MySQL)
 ├── schemas.py                  → Pydantic: DeployRequest, DraftSaveRequest, ImageResponse
 ├── nats_producer.py            → Cliente NATS JetStream (publish deploy/destroy)
 ├── routers/
 │    ├── slice_router.py        → CRUD de borradores + endpoints utilitarios de workers/AZs
 │    ├── deploy_router.py       → POST deploy, DELETE destroy
 │    └── image_router.py        → Gestión de imágenes (NFS + OpenStack Glance)
 ├── repositories/
 │    └── slice_repo.py          → SliceRepository.save_draft()
 └── services/
      ├── placement_worker.py    → Worker async: Servers' State → placement → enriquecimiento → NATS
      ├── nats_listener.py       → Listener async: slice.result → actualiza MySQL
      └── gc_scheduler.py        → Garbage collector de ISOs, discos y imágenes huérfanas
```

---

## Autenticación y roles

El API Gateway valida el JWT de Keycloak y añade dos headers internos antes de reenviar:
- `X-User-Id`: `sub` del token (UUID del usuario en Keycloak)
- `X-User-Role`: rol de mayor prioridad extraído de `realm_access.roles`

El Slice Manager lee esos headers a través de la dependencia `get_current_user()` (en `auth.py`).
Los routers que los requieren reciben un objeto `CurrentUser(user_id, role)`.

### Roles reconocidos (de menor a mayor privilegio)

| Rol | Descripción |
|-----|-------------|
| `usuario` | Estudiante — gestiona sus propios slices e imágenes |
| `jefeProyecto` | Docente / jefe — puede ver slices de su proyecto |
| `admin` | Administrador — acceso total, puede ver todos los slices e imágenes |
| `superAdmin` | Superadministrador — igual que admin, nivel máximo |

`admin` y `superAdmin` pueden ver y gestionar recursos de cualquier usuario.

---

## Zonas de disponibilidad (Availability Zones)

El sistema soporta múltiples zonas de disponibilidad. Cada zona tiene su propio conjunto de workers:

| `id` | Nombre | Backend |
|------|--------|---------|
| `1` | Linux Cluster | Workers QEMU/KVM con OVS |
| `2` | OpenStack | Nova + Neutron via openstacksdk |

El `availability_zone_id` viaja en el mensaje de deploy y determina qué executor usa el
Compute Provisioner y el Network Orchestrator (Strategy Pattern).

---

## Servers' State multidimensional

Al recibir una orden de deploy, el `placement_worker` construye el Servers' State desde BD
con tres dimensiones independientes por worker:

```
OC_r[j]      — factor de overcommit por recurso y worker (calculado por Observabilidad,
                 o valor de arranque: OC_cpu=2.0, OC_ram=1.54, OC_disco=1.0)

C_ef_r[j]  = C_nominal_r[j] × OC_r[j]
disp_r[j]  = C_ef_r[j]  −  Σ recurso_r(VMs ACTIVE en worker_j)
```

El resultado es una lista de workers con `{ worker_id, disponible_cpu, disponible_ram, disponible_disco }`.
Solo se incluyen workers de la zona solicitada, excluyendo siempre el headnode (`id=1`).

---

## Flujo completo de deploy

```
Frontend
    │ POST /api/v1/slices/{id}/deploy
    │ { availability_zone_id, ttl_hours }
    ▼
deploy_router.py
    ├─ Valida que el slice exista en MySQL
    ├─ Cambia status → PENDING_APPROVAL
    └─ Encola en placement_queue { slice_id, zone_id }
       Responde 202 inmediatamente
    ▼
placement_worker.py (background)
    │
    ├─ 1. Lee VMs del slice desde MySQL
    ├─ 2. Construye Servers' State (cpu/ram/disco) con workers de la zona
    ├─ 3. POST http://vm-placement:8080/placement → mapa vm→worker
    │
    │  Si placement == SUCCESS:
    ├─ 4. Genera TAP de gestión por VM (eth0): t-{slice[-3:]}-{vm[:4]}-m
    ├─ 5. Para cada edge: genera TAPs de datos, MACs, asigna VLAN libre (100–4000)
    ├─ 6. Por cada VM: asigna VNC port libre, resuelve image_path, asigna IPs
    ├─ 7. Guarda deployed_vms y deployed_links en slice_json (MySQL)
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
    ├─ Si status == DRAFT: elimina VMs + Slice de MySQL → responde 200
    └─ Si status != DRAFT:
        ├─ Lee deployed_vms y deployed_links de slice_json
        ├─ Publica slice.destroy en NATS JetStream
        ├─ Libera VLANs de MySQL
        ├─ Cambia status → TERMINATED
        └─ Cambia state de todas las VMs → TERMINATED
```

---

## API REST

| Método | Ruta | Auth | Descripción |
|--------|------|------|-------------|
| `GET` | `/api/v1/slices/` | usuario+ | Lista slices del usuario (admin ve todos) |
| `POST` | `/api/v1/slices/draft` | usuario+ | Crea un borrador con topología del canvas |
| `PUT` | `/api/v1/slices/{id}/draft` | usuario+ | Actualiza un borrador existente |
| `POST` | `/api/v1/slices/{id}/deploy` | usuario+ | Inicia el despliegue de un borrador |
| `DELETE` | `/api/v1/slices/{id}` | usuario+ | Destruye un slice activo o elimina un borrador |
| `GET` | `/api/v1/slices/utils/images` | usuario+ | Lista imágenes disponibles (BD + OpenStack Glance) |
| `POST` | `/api/v1/slices/utils/images/upload` | usuario+ | Sube imagen al NFS o a Glance |
| `DELETE` | `/api/v1/slices/utils/images/{id}` | usuario+ | Elimina imagen de BD y disco/Glance |
| `GET` | `/api/v1/slices/utils/images/unused` | usuario+ | Lista imágenes sin VMs activas (para GC) |
| `POST` | `/api/v1/slices/utils/images/gc/run` | admin+ | Lanza ciclo de GC manualmente |
| `GET` | `/api/v1/slices/utils/workers` | usuario+ | Lista workers registrados |
| `GET` | `/api/v1/slices/utils/available-ips` | usuario+ | Lista IPs del pool sin asignar |
| `GET` | `/` | No | Healthcheck básico |

---

## Gestión de imágenes (catálogo híbrido)

`GET /api/v1/slices/utils/images` devuelve la unión de:
1. Imágenes registradas en la BD local (con JOIN a `AvailabilityZone`).
2. Imágenes de OpenStack Glance que no existen aún en BD — se sincronizan automáticamente
   creando un registro en MySQL con `path = glance://<UUID>` y `availability_zone_id = OPENSTACK_AZ_ID`.

`POST /utils/images/upload` con `availability_zone_id = OPENSTACK_AZ_ID`:
- Guarda el archivo temporalmente en `/tmp`.
- Lo sube a Glance via openstacksdk.
- Persiste `path = glance://<UUID>` en BD.
- Elimina el archivo temporal.

---

## Formato del payload al VM Placement

```json
{
  "slice_id": "42",
  "availability_zone": "1",
  "vms": [
    { "vm_id": "n214", "vcpus": 2.0, "ram_gb": 0.5, "disco_gb": 10.0 },
    { "vm_id": "n215", "vcpus": 1.0, "ram_gb": 0.25, "disco_gb": 5.0 }
  ],
  "workers": [
    { "worker_id": 2, "disponible_cpu": 12.5, "disponible_ram": 28.3, "disponible_disco": 180.0 },
    { "worker_id": 3, "disponible_cpu": 10.0, "disponible_ram": 24.0, "disponible_disco": 200.0 }
  ]
}
```

---

## Formato del mensaje NATS `slice.deploy`

```json
{
  "slice_id": "42",
  "request_id": "req-a1b2c3d4",
  "availability_zone_id": 1,
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
      "vm1_ssh_private_key": "...",
      "vm2_id": "n215",
      "vm2_worker_ip": "10.0.10.3",
      "vm2_tap": "t-042-n215-n214",
      "vm2_ssh_user": "ubuntu",
      "vm2_ssh_private_key": "..."
    }
  ]
}
```

> El TAP `t-042-n214-m` (sufijo `-m`) es la interfaz de gestión (eth0).
> Se genera siempre, independientemente de si la VM tiene enlaces de datos.

---

## Esquema de generación de MACs

```
52:54:00 : XX : YY : ZZ

  52:54:00  → prefijo QEMU estándar
  XX:YY     → primeros 4 chars del SHA-256 del slice_id
  ZZ        → contador secuencial global dentro del slice (incluye TAP de gestión)
```

---

## Esquema de IPs internas (subred de gestión)

```
subred = 10.(slice_id // 256).(slice_id % 256).0/24
VMs    = 10.X.Y.10, 10.X.Y.11, ...

Ejemplos:
  slice_id=1  → 10.0.1.10, 10.0.1.11, ...
  slice_id=42 → 10.0.42.10, 10.0.42.11, ...
```

---

## Base de datos MySQL

| Tabla | Descripción |
|-------|-------------|
| `slices` | Estado y metadatos del slice, `slice_json` con topología y `deployed_vms`/`deployed_links` |
| `vms` | VMs con recursos, worker asignado, `vnc_port`, `external_ip`, `internet_access` |
| `vlans` | VLANs asignadas por slice (se limpian al destroy o rollback) |
| `images` | Imágenes disponibles con `path` (ruta NFS o `glance://<UUID>`) y `availability_zone_id` |
| `workers` | Workers con IP, zona, `cpu`, `ram` (MB), `disk_gb`, `oc_cpu`, `oc_ram`, `oc_disco` |
| `ip_pool` | Pool de IPs externas flotantes: `is_used`, `vm_id` (FK) |
| `availability_zones` | Zonas de disponibilidad — el deploy filtra workers por `id` |

---

## Inventario de workers y llaves SSH

Las credenciales SSH se leen desde archivos montados en el contenedor:

```
slice-manager/
└── keys/
    ├── id_ed25519        → llave para gateway/headnode (VNC y SSH jumphost)
    ├── worker2.pem       → worker_id=2
    ├── worker3.pem       → worker_id=3
    └── worker4.pem       → worker_id=4
```

El worker con `id=1` es el headnode — corre los servicios y **no recibe VMs**.

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
| `IMAGES_DIR` | `/mnt/cloud_images` | Directorio NFS de imágenes del Linux Cluster |
| `OPENSTACK_AZ_ID` | `2` | ID de la availability zone de OpenStack en BD |
| `OS_AUTH_URL` | — | Endpoint Keystone de OpenStack |
| `OS_USERNAME` | `admin` | Usuario OpenStack |
| `OS_PASSWORD` | — | Contraseña OpenStack |
| `OS_PROJECT_NAME` | `admin` | Proyecto OpenStack |
| `OS_USER_DOMAIN_NAME` | `Default` | Dominio de usuario OpenStack |
| `OS_PROJECT_DOMAIN_NAME` | `Default` | Dominio de proyecto OpenStack |

---

## Despliegue con Docker

```bash
# 1. Colocar las llaves PEM en ./keys/
mkdir -p keys
# Copiar worker2.pem, worker3.pem, worker4.pem, id_ed25519

# 2. Levantar
docker compose up -d --build

# 3. Verificar
curl http://localhost:8000/
# → {"status": "✅ Slice Manager Online (Modo Híbrido)"}

# 4. Logs
docker compose logs -f slice-manager
```

> **Nota:** El Slice Manager se conecta a MySQL. Asegurarse de que MySQL esté corriendo antes de levantar.
