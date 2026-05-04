# Slice Manager — PUCP Cloud Orchestrator

Director de orquestación del sistema. Recibe topologías del frontend (canvas),
las persiste en MySQL, coordina el VM Placement, enriquece el contrato de despliegue
y publica las órdenes al Queue Manager via NATS JetStream. También escucha los
resultados y ejecuta rollback automático ante fallos.

---

## Responsabilidades

- Exponer la API REST que consume el frontend (borradores, deploy, destroy).
- Persistir slices y VMs en MySQL (modelo relacional).
- Consultar métricas reales de los workers desde Prometheus.
- Invocar al VM Placement para obtener el mapa `vm → worker`.
- Enriquecer el contrato con: IPs SSH, llaves PEM, MACs, TAP names, VLANs, VNC ports, rutas de imagen.
- Publicar `slice.deploy` y `slice.destroy` en NATS JetStream hacia el Queue Manager.
- Escuchar `slice.result` y actualizar el estado en MySQL.
- Ejecutar rollback automático (publish destroy) si el resultado es error.

**No** ejecuta comandos en workers — eso es del Compute Provisioner.  
**No** configura red — eso es del Network Orchestrator.  
**No** decide el algoritmo de placement — eso es del VM Placement.

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
      ├── placement_worker.py    → Worker async: placement → enriquecimiento → NATS
      ├── nats_listener.py       → Listener async: slice.result → actualiza MySQL
      └── telemetry.py           → Consulta Prometheus (fallback a datos simulados)
```

---

## Flujo completo de deploy

```
Frontend
    │ POST /api/v1/slices/{id}/deploy
    │ { availability_zone, ttl_hours, motivo }
    ▼
deploy_router.py
    ├─ Valida que el slice exista en MySQL
    ├─ Cambia status → PENDING_APPROVAL
    └─ Encola en placement_queue (asyncio.Queue)
    │  Responde 202 inmediatamente
    ▼
placement_worker.py (background)
    │
    ├─ 1. Lee VMs del slice desde MySQL (tabla vms)
    ├─ 2. Consulta Prometheus → métricas de workers disponibles
    ├─ 3. POST http://vm-placement:8080/placement → mapa vm→worker
    │
    │  Si placement == SUCCESS:
    ├─ 4. Para cada edge del slice_json:
    │       - Genera tap names: t-{slice[-3:]}-{vm1[:4]}-{vm2[:4]}
    │       - Genera MACs: 52:54:XX:YY:{counter:02x} (XX:YY = SHA256 del slice_id)
    │       - Asigna VLAN libre aleatoria (100–4000, sin colisión con BD)
    │       - Persiste Vlan en MySQL
    │       - Lee llave PEM desde ./keys/workerN.pem
    │
    ├─ 5. Para cada VM:
    │       - Asigna VNC port libre por worker (5901–5999, sin colisión con BD)
    │       - Consulta image_path desde tabla images
    │       - Construye VMSpec completo
    │
    ├─ 6. Guarda deployed_vms y deployed_links en slice_json (MySQL)
    ├─ 7. Publica slice.deploy en NATS JetStream → Queue Manager
    └─ 8. Cambia status → PROVISIONING
    ▼
Queue Manager → Compute + Network → slice.result
    ▼
nats_listener.py (background)
    ├─ status == "success" → slice.status = ACTIVE
    └─ status != "success" → slice.status = FAILED
                              publica slice.destroy (rollback automático)
```

---

## Flujo de destroy

```
Frontend
    │ DELETE /api/v1/slices/{id}
    ▼
deploy_router.py
    ├─ Si status == DRAFT: elimina VMs + slice de MySQL → responde 200
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
| `GET` | `/` | Healthcheck básico |

---

## Formato de mensajes NATS

### slice.deploy (publicado al Queue Manager)

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
      "image_path": "/images/cirros-0.5.1-x86_64-disk.img",
      "vnc_port": 5901,
      "vnc_display": 1,
      "tap_interfaces": [
        { "tap_name": "t-042-n214-n215", "mac": "52:54:00:A3:C7:00" }
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
      "vm1_security_rules": [],
      "vm2_id": "n215",
      "vm2_worker_ip": "10.0.10.3",
      "vm2_tap": "t-042-n215-n214",
      "vm2_ssh_user": "ubuntu",
      "vm2_ssh_private_key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----",
      "vm2_security_rules": []
    }
  ]
}
```

### slice.destroy (publicado al Queue Manager)

Mismo formato que `slice.deploy` pero sin campos opcionales de imagen/vnc.
Las `vms` y `links` se recuperan de `slice_json.deployed_vms` y `slice_json.deployed_links`
guardados en MySQL durante el deploy.

---

## Esquema de generación de MACs

```
52:54:00 : XX : YY : ZZ

  52:54:00  → prefijo QEMU estándar
  XX:YY     → primeros 4 chars del SHA-256 del slice_id
  ZZ        → contador secuencial global (0x00–0xFF)
```

Ejemplo para `slice_id=42`:
```
52:54:00:A3:C7:00   → primer TAP del slice 42
52:54:00:A3:C7:01   → segundo TAP del slice 42
```

## Esquema de generación de TAP names

```
t-{slice_id[-3:]}-{vm1_id[:4]}-{vm2_id[:4]}
t-{slice_id[-3:]}-{vm2_id[:4]}-{vm1_id[:4]}
```

Ejemplo: slice 42, VMs `n214` ↔ `n215`:
```
vm1 side: t-042-n214-n215
vm2 side: t-042-n215-n214
```

---

## Base de datos MySQL

Tablas principales utilizadas por este módulo:

| Tabla | Descripción |
|-------|-------------|
| `slices` | Estado y metadatos del slice, incluye `slice_json` (JSON) |
| `vms` | VMs con recursos, worker asignado, vnc_port |
| `vlans` | VLANs asignadas por slice (se limpian al destroy) |
| `images` | Imágenes disponibles con `path` absoluto en el worker |
| `workers` | Workers registrados con IP |
| `users`, `projects` | IAM — usados por routers (actualmente hardcodeado `user-123`) |

El campo `slice_json` almacena la topología del canvas y, tras el deploy, también
`deployed_vms` y `deployed_links` (la "receta" completa para poder hacer destroy).

---

## Inventario de workers y llaves SSH

Las credenciales SSH se leen desde archivos `.pem` montados en el contenedor:

```
slice-manager/
└── keys/
    ├── worker1.pem
    ├── worker2.pem
    ├── worker3.pem
    └── worker4.pem
```

El mapeo `worker_id → IP` está hardcodeado en `placement_worker.py`:

| worker_id | IP | Usuario |
|-----------|-----|---------|
| 1 | 10.0.10.1 | ubuntu |
| 2 | 10.0.10.2 | ubuntu |
| 3 | 10.0.10.3 | ubuntu |
| 4 | 10.0.10.4 | ubuntu |

---

## Variables de entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `NATS_URL` | `nats://localhost:4222` | URL del servidor NATS (Queue Manager) |
| `VM_PLACEMENT_URL` | `http://vm-placement:8080/placement` | URL del VM Placement |
| `PROMETHEUS_URL` | `http://10.0.10.1:9090` | URL de Prometheus |
| `DB_HOST` | `mysql-db` | Host de MySQL |
| `DB_USER` | `root` | Usuario MySQL |
| `DB_PASSWORD` | `root` | Contraseña MySQL |
| `DB_NAME` | `cloud` | Base de datos MySQL |

---

## Despliegue con Docker

```bash
# 1. Colocar las llaves PEM en ./keys/
mkdir -p keys
cp /ruta/a/worker1.pem keys/worker1.pem
# ... repetir para worker2, worker3, worker4

# 2. Levantar
docker compose up -d

# 3. Verificar
curl http://localhost:8000/
# → {"status": "✅ Slice Manager Online (Modo Híbrido)"}

# 4. Logs
docker compose logs -f slice-manager
```

> **Nota:** El Slice Manager se conecta a MySQL en `host.docker.internal` (la
> máquina host). Asegurarse de que MySQL esté corriendo y accesible antes de levantar.

---

## Telemetría y fallback

El módulo consulta Prometheus para obtener métricas reales de los workers antes de
cada placement:

- `node_memory_MemAvailable_bytes` → RAM disponible en MB
- `count(node_cpu_seconds_total{mode="idle"})` → vCPUs disponibles por instancia
- `node_filesystem_avail_bytes{mountpoint="/"}` → disco disponible en GB

Si Prometheus no responde (timeout 5s), usa métricas simuladas para no bloquear
el pipeline: 10 vCPUs, 16 GB RAM, 500 GB disco por worker.

---

## Script de prueba de concurrencia

`test_concurrency.py` dispara 20 requests simultáneos al endpoint de deploy
para validar que la `asyncio.Queue` los serializa correctamente sin que el
servidor colapse:

```bash
python test_concurrency.py
```

El resultado esperado: todos los requests reciben `202 ACCEPTED` inmediatamente
(< 1s cada uno) y el placement_worker los procesa uno por uno en background.
