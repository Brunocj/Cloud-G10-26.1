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
- Construir el Servers' State multidimensional (cpu/ram/disco) desde BD al momento del deploy,
  **para ambas zonas** (incluye el `host_name` de cada worker para el BYOS de OpenStack).
- Invocar al VM Placement (HTTP) para obtener el mapa `vm → worker` (+ `selected_host`).
- Enriquecer el contrato con: IPs SSH, llaves PEM, MACs, TAP names (incl. gestión),
  **VLANs** (mgmt + C-VID por enlace + S-VID por slice si Q-in-Q), **reglas de firewall**
  del usuario por VM, VNC ports, rutas de imagen, flavor, credenciales cloud-init, IPs internas.
- Gestionar **flavors** (plantillas vCPU/RAM/disco con visibilidad global/privado/proyecto)
  y materializarlos en Nova (eager para admin, lazy para el resto).
- Reservar la **VLAN de gestión** del slice en la tabla `vlans` y (OpenStack) las VLANs
  del pool `[OS_VLAN_MIN, OS_VLAN_MAX]` que se fuerzan como `segmentation_id` en Neutron.
- Publicar `slice.deploy` y `slice.destroy` en NATS JetStream hacia el Workflow Orchestrator.
- Escuchar `slice.result` y actualizar el estado en MySQL; rollback automático ante error.
- Gestionar el pool de IPs externas (tabla `ip_pool`), imágenes (NFS + Glance), y el resto
  del ciclo de vida (aprobaciones, TTL, kill switch, bitácora, consumo por proyecto).

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
 │    ├── slice_router.py        → CRUD de borradores (+ snapshot de flavor a la VM)
 │    ├── deploy_router.py       → POST deploy, DELETE destroy, Modo Edición (shrink/extend)
 │    ├── image_router.py        → Gestión de imágenes (NFS + OpenStack Glance)
 │    ├── flavor_router.py       → CRUD de flavors (visibilidad por rol, materialización Nova)
 │    ├── approval_router.py     → Flujo de aprobación de despliegues
 │    ├── project_router.py      → Proyectos + consumo de recursos por proyecto
 │    ├── user_router.py         → Gestión de usuarios / perfil
 │    ├── audit_router.py        → Bitácora de eventos
 │    ├── infra_router.py        → Gestión de infraestructura (superAdmin)
 │    ├── maintenance_router.py  → Limpieza de BD de slices/VMs (admin/superAdmin)
 │    └── notification_router.py → Notificaciones (WebSockets)
 ├── repositories/
 │    └── slice_repo.py          → SliceRepository.save_draft()
 └── services/
      ├── placement_worker.py    → Worker async: Servers' State → placement → enriquecimiento → NATS
      ├── slice_destroyer.py     → Lógica compartida de destroy (manual/TTL/kill switch)
      ├── nats_listener.py       → Listener async: slice.result → actualiza MySQL / rollback
      ├── ttl_scheduler.py       → Auto-destrucción de slices al vencer su TTL
      ├── stuck_ops_scheduler.py → Vigía de destroys/modifies colgados sin confirmación
      ├── gc_scheduler.py        → Garbage collector de ISOs, discos e imágenes huérfanas
      └── db_maintenance.py      → Detección/limpieza de filas huérfanas o inconsistentes en BD
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

## Gate de elegibilidad por uso en vivo

Antes de construir el Servers' State, `placement_worker` consulta
`GET http://observability:8006/metrics/workers` y excluye workers cuyo uso
reportado supere los umbrales configurados:

```
worker excluido si:  live_ram_usage_pct > MAX_RAM_USAGE_PCT (default 90)
                   O  live_cpu_usage_pct > MAX_CPU_USAGE_PCT (default 95)
```

Los umbrales son variables de entorno (`MAX_RAM_USAGE_PCT`, `MAX_CPU_USAGE_PCT`)
definidas en `placement_worker.py`. Si Observability no responde (timeout 3s),
el gate queda inactivo y no se excluye a ningún worker (fallback permisivo).
Si tras el filtro no queda ningún worker elegible en la zona, el slice pasa
directamente a `FAILED` sin invocar al VM Placement.

---

## Servers' State multidimensional

Al recibir una orden de deploy, el `placement_worker` construye el Servers' State desde BD
con tres dimensiones independientes por worker, **usando solo los workers que pasaron
el gate de elegibilidad por uso**:

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
    ├─ 2. Consulta Observability (/metrics/workers) y excluye workers sobrecargados
    ├─ 3. Construye Servers' State (cpu/ram/disco) con workers elegibles de la zona
    ├─ 4. POST http://vm-placement:8080/placement → mapa vm→worker
    │
    │  Si placement == SUCCESS:
    ├─ 5. Genera TAP de gestión por VM (eth0): t-{slice[-3:]}-{vm[:4]}-m
    ├─ 6. Para cada edge: genera TAPs de datos, MACs, asigna VLAN libre (100–4000)
    ├─ 7. Por cada VM: asigna VNC port libre, resuelve image_path, asigna IPs
    ├─ 8. Guarda deployed_vms y deployed_links en slice_json (MySQL)
    ├─ 9. Publica slice.deploy en NATS JetStream → Queue Manager
    └─ 10. Cambia status → PROVISIONING (o FAILED si el publish falló)
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
        ├─ Cambia status → TERMINATING (no libera recursos todavía)
        ├─ Re-inyecta llaves SSH frescas (worker de la zona con llave válida)
        ├─ Publica slice.destroy en NATS JetStream (incluye compute_ssh_map en OpenStack)
        └─ Al confirmar slice.result (nats_listener):
             ├─ éxito → libera VLANs/IPs, status → TERMINATED
             └─ error → revierte al estado anterior (reintentable) / FAILED

Si la confirmación NUNCA llega (QM caído a mitad de la saga, payload inválido,
resultado publicado con el SliceManager abajo), el slice quedaría en TERMINATING
para siempre. De eso se encarga stuck_ops_scheduler.py:
    ├─ pasados STUCK_OP_TIMEOUT_MINUTES → republica slice.destroy
    │   (hasta STUCK_DESTROY_MAX_ATTEMPTS intentos)
    └─ agotados los intentos → vuelve al estado previo y notifica al dueño
        (sin liberar VLANs/IPs: la infra puede seguir viva → limpieza manual)
```

> El destroy **espera la confirmación real** de la limpieza física antes de liberar
> VLANs/IPs (estado `TERMINATING`). Antes se liberaba al publicar el mensaje, lo que
> podía dejar la BD diciendo "libre" mientras la infraestructura seguía viva.

> Ningún camino marca `TERMINATED` ni libera recursos por *timeout*: sin confirmación
> no se sabe si la VLAN sigue configurada en un worker, y reutilizarla causaría una
> colisión de Capa 2 entre slices.

**Variables de entorno del vigía** (`stuck_ops_scheduler`):

| Variable | Default | Descripción |
|---|---|---|
| `STUCK_OP_CHECK_INTERVAL_SECONDS` | `60` | Cada cuánto revisa operaciones colgadas |
| `STUCK_OP_TIMEOUT_MINUTES` | `10` | Antigüedad a partir de la cual una operación se da por colgada |
| `STUCK_DESTROY_MAX_ATTEMPTS` | `3` | Intentos totales de destroy antes de revertir |

---

## Mantenimiento de BD (`db_maintenance.py`)

Herramienta a demanda (no un scheduler) para auditar y corregir filas de MySQL
que quedaron sueltas por bugs ya corregidos, ediciones manuales de la BD, o
saltos de estado sin pasar por el flujo normal. **No toca infraestructura
física** (SSH/NATS) — solo bookkeeping en MySQL.

Categorías corregibles vía `POST /api/v1/maintenance/clean`:

| Categoría | Qué detecta | Corrección |
|---|---|---|
| `orphan_vms` | VM cuyo `slice_id` ya no existe en `slices` | Libera su IP externa y borra la fila |
| `orphan_vlans` | VLAN cuyo `slice_id` ya no existe en `slices` | Borra la fila |
| `stale_ip_pool` | IP con `is_used=1` sin VM viva detrás | `is_used=0`, `vm_id=NULL` |
| `zombie_vms` | VM en estado vivo cuyo slice ya es TERMINATED/FAILED/REJECTED | Libera su IP y alinea `vm.state` con el slice |
| `terminated_vms` | VM en estado `TERMINATED` (historial de instancias ya destruidas) | Libera su IP (si quedó alguna) y borra la fila |
| `empty_drafts` | Slice `DRAFT` sin ninguna VM asociada | Borra la fila |

`terminated_vms` no toca las VMs `FAILED` (quedan como evidencia de despliegues
fallidos) ni el slice al que pertenecían (el historial del slice se sirve
desde `slice.slice_json`, no desde la tabla `vms` — purgar estas filas no
afecta lo que ve el usuario).

`GET /api/v1/maintenance/scan` (admin+) devuelve el reporte de solo lectura,
incluyendo además `stuck_operations` (informativo: TERMINATING/PROVISIONING
más viejos que `STUCK_OP_TIMEOUT_MINUTES`) — esa categoría **nunca** se
corrige desde `clean`, porque ya la maneja `stuck_ops_scheduler` con la
lógica segura de reintentos/reversión (ver sección anterior); forzarla acá
podría liberar VLANs/IPs de infraestructura que todavía sigue viva.

`POST /api/v1/maintenance/clean` (superAdmin) acepta `{ categories, dry_run }`;
con `dry_run=true` (default) solo devuelve cuántas filas se tocarían, sin
escribir en la BD.

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
| `GET` | `/api/v1/maintenance/scan` | admin+ | Reporte de filas huérfanas/inconsistentes (solo lectura) |
| `POST` | `/api/v1/maintenance/clean` | superAdmin | Corrige las inconsistencias detectadas (`dry_run=true` por defecto) |
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
  "availability_zone_id": 1,
  "vms": [
    { "vm_id": "n214", "vcpus": 2.0, "ram_gb": 0.5, "disco_gb": 10.0 },
    { "vm_id": "n215", "vcpus": 1.0, "ram_gb": 0.25, "disco_gb": 5.0 }
  ],
  "workers": [
    { "worker_id": 2, "disponible_cpu": 12.5, "disponible_ram": 28.3, "disponible_disco": 180.0, "host_name": "worker1" },
    { "worker_id": 3, "disponible_cpu": 10.0, "disponible_ram": 24.0, "disponible_disco": 200.0, "host_name": "worker3" }
  ]
}
```

> El Servers' State se construye **igual para ambas zonas** (Linux y OpenStack).
> `host_name` es el nombre físico del host (host de Nova en OpenStack) que el
> placement devuelve como `selected_host` para el BYOS. Así VM Placement es
> agnóstico y no consulta a Nova directamente.

## Asignación de VLANs

- **Linux Cluster:** la VLAN de gestión (`mgmt_vlan = 1000 + slice_id`) se reserva en
  la tabla `vlans` (type='M') y el C-VID de cada enlace se sortea global-único
  (100–4000). Con `QINQ_ENABLED=true`, además se asigna un **S-VID por slice** y el
  Network Orchestrator interpone el `dot1q-tunnel`.
- **OpenStack:** mgmt y C-VIDs salen de un **pool único** `[OS_VLAN_MIN, OS_VLAN_MAX]`
  (default 11–900, = `network_vlan_ranges` del physnet), todos únicos entre sí, para
  **forzarlos como `segmentation_id`** en Neutron (la tabla `vlans` coincide con el cable).
- Las reglas de firewall del usuario (por nodo, `firewall_rules`) se adjuntan a cada
  extremo del enlace (`vmN_security_rules`) para que el Network Orchestrator las aplique.

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
| `vms` | VMs con recursos, worker asignado, `vnc_port`, `external_ip`, `internet_access`, `flavor_id`/`flavor_name` (snapshot), `provider_instance_id`, `vnc_url` |
| `vlans` | VLANs por slice: `type` = M (gestión) / C (C-VID enlace) / S (S-VID Q-in-Q) / p2p (legado); `vlan_number` = número real. Se limpian al destroy/rollback |
| `flavors` | Plantillas vCPU/RAM/disco con `visibility` (global/private/project), `owner_user_id`, `provider_flavor_id` (UUID Nova), soft-delete |
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
| `OBSERVABILITY_URL` | `http://observability:8006` | URL de Observability (consulta de uso en vivo) |
| `MAX_CPU_USAGE_PCT` | `95` | Umbral de CPU% en vivo — workers por encima quedan excluidos del placement |
| `MAX_RAM_USAGE_PCT` | `90` | Umbral de RAM% en vivo — workers por encima quedan excluidos del placement |
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
| `QINQ_ENABLED` | `false` | Activa Q-in-Q (asigna S-VID). En el estado actual, solo Linux |
| `OS_VLAN_MIN` / `OS_VLAN_MAX` | `11` / `900` | Rango del pool de VLANs de OpenStack (= `network_vlan_ranges` del physnet) |

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
