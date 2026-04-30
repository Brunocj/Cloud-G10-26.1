# 🍰 Slice Manager - Orquestador Principal de la Plataforma Cloud

**Slice Manager** es el microservicio **API Gateway** y **Orquestador de Nivel Superior** del ecosistema Cloud-G10-26.1. Es responsable de recibir las solicitudes de despliegue de infraestructura desde el frontend, validar las topologías de red y máquinas virtuales, gestionar el ciclo de vida del recurso en base de datos, y **orquestar el patrón Saga** delegando la ejecución física a través de colas asincrónicas (NATS JetStream).

> **En otras palabras:** Es el "director de orquesta" que traduce lo que el usuario dibuja en el canvas en comandos físicos que se ejecutan en los servidores reales.

---

## 📋 Tabla de Contenidos

- [Visión General](#visión-general)
- [Rol en la Arquitectura](#-rol-en-la-arquitectura)
- [Responsabilidades Clave](#-responsabilidades-clave)
- [Estructura del Módulo](#-estructura-del-módulo)
- [Stack Tecnológico](#-stack-tecnológico)
- [Endpoints de la API](#-endpoints-de-la-api)
- [Flujo de Datos Detallado](#-flujo-de-datos-detallado)
- [Modelos de Datos (ORM)](#-modelos-de-datos-orm)
- [Esquemas Pydantic](#-esquemas-pydantic)
- [Configuración y Variables de Entorno](#-configuración-y-variables-de-entorno)
- [Instalación y Despliegue](#-instalación-y-despliegue)
- [Cómo Funciona Internamente](#-cómo-funciona-internamente)
- [Integración con NATS](#-integración-con-nats)
- [Integración con VM Placement](#-integración-con-vm-placement)
- [Integración con Telemetría (Prometheus)](#-integración-con-telemetría-prometheus)
- [Seguridad y Manejo de Credenciales](#-seguridad-y-manejo-de-credenciales)
- [Manejo de Errores y Tolerancia a Fallos](#-manejo-de-errores-y-tolerancia-a-fallos)
- [Roadmap y Mejoras Futuras](#-roadmap-y-mejoras-futuras)

---

## 🌍 Visión General

Slice Manager es el **punto de entrada único** (single entry point) para todas las operaciones de provisioning de infraestructura en la plataforma. Un "Slice" representa una **topología de red completa** compuesta por máquinas virtuales, enlaces de red y configuraciones de seguridad.

### Ciclo de Vida de un Slice

```
┌──────────┐      ┌─────────────────┐      ┌──────────────┐      ┌────────┐
│  DRAFT   │─────▶│ PENDING_APPROVAL│─────▶│ PROVISIONING │─────▶│ ACTIVE │
└──────────┘      └─────────────────┘      └──────────────┘      └────────┘
      │                     │                       │                  │
      │                     │                       ▼                  │
      │                     │                   ┌────────┐              │
      └─────────────────────┴──────────────────▶│ FAILED │◀────────────┘
                                               └────────┘
      │
      └──────────────────────────────────────────────────▶ ┌───────────┐
                                                            │TERMINATED│
                                                            └───────────┘
```

---

## 🏛️ Rol en la Arquitectura

El Slice Manager ocupa un lugar central en la orquestación de recursos. Implementa una **arquitectura de comunicación híbrida**:

```
┌──────────────────────────────────────────────────────────────┐
│                    FRONTEND / Cliente Web                      │
└────────────────────────────┬─────────────────────────────────┘
                             │ (HTTP REST)
                             ▼
         ┌─────────────────────────────────────────┐
         │         SLICE MANAGER                   │
         │      (Orquestador Central)              │
         │ ════════════════════════════════════    │
         │ • API Gateway (FastAPI)                 │
         │ • Persistencia (SQLAlchemy + MySQL/DB)  │
         │ • Cola de Procesamiento (asyncio.Queue) │
         │ • Publicador de Eventos (NATS)          │
         └────────┬──────────┬──────────┬──────────┘
                  │          │          │
         ┌────────▼─┐  ┌─────▼──────┐ ┌▼───────────┐
         │ VM       │  │ Prometheus │ │ NATS       │
         │Placement │  │(Telemetría)│ │(Colas)    │
         │(Matching)│  └────────────┘ │           │
         └──────────┘                 │           │
                                      │           │
                                      ▼           ▼
                        ┌──────────────────────────────────┐
                        │  ComputeProvisioner              │
                        │  NetworkOrchestrator             │
                        │  (Workers en Background)         │
                        └──────────────────────────────────┘
                                      │
                                      ▼
                        ┌──────────────────────────────────┐
                        │  Infraestructura Física          │
                        │  • KVM/QEMU (VMs)               │
                        │  • Bridges (Redes)              │
                        │  • Storage (Images)             │
                        └──────────────────────────────────┘
```

**Capas de Comunicación:**

| Capa | Protocolo | Dirección | Propósito |
|------|-----------|-----------|----------|
| **Frontend ↔ Slice Manager** | HTTP/REST | Bidireccional | Recepción de topologías y estado |
| **Slice Manager ↔ VM Placement** | HTTP/REST | Síncrono | Decisión de placement (matching) |
| **Slice Manager ↔ Prometheus** | HTTP/REST | Síncrono | Consulta de métricas en vivo |
| **Slice Manager ↔ Queue Manager** | NATS JetStream | Asíncrono | Delegación de tareas físicas |
| **Queue Manager ↔ Provisioners** | NATS JetStream | Asíncrono | Orquestación de provisioning |

---

## 🎯 Responsabilidades Clave

### 1. **Gestión de Estado de Slices**
- Almacena el ciclo de vida completo en base de datos relacional (MySQL/SQLite).
- Estados: `DRAFT`, `PENDING_APPROVAL`, `PROVISIONING`, `ACTIVE`, `FAILED`, `TERMINATED`.
- Permite recuperar históricos y auditoría de operaciones.

### 2. **Validación de Topologías (FU-01)**
- Analiza el `topology_json` del frontend buscando consistencia del grafo.
- Verifica que todos los nodos tengan conectividad apropiada.
- Rechaza topologías inválidas antes de intentar provisioning.

### 3. **Extracción Dinámica de Recursos**
- Parsea el JSON de la topología para extraer:
  - **VMs:** ID, vCPUs, RAM (MB), Disco (GB).
  - **Edges (enlaces):** Conexiones lógicas entre VMs.
  - **Configuración:** Nombres, descripciones, parámetros.

### 4. **Consulta de Telemetría en Vivo**
- Conecta con Prometheus para obtener métricas reales de los hipervisores:
  - CPU disponible (vCPUs libres).
  - Memoria RAM disponible (MB).
  - Espacio en disco disponible (GB).
- Implementa **fallback automático** a datos simulados si Prometheus falla.

### 5. **Decisión de Placement (VM Placement Mapping)**
- Envía requerimientos de VM + métricas de workers al servicio **VM Placement**.
- Recibe la decisión de asignación: `vm-id → server-id`.
- Garantiza que cada VM se aloje en un servidor con recursos suficientes.

### 6. **Enriquecimiento de Contratos**
- Toma la decisión de placement y la cruza con el inventario de servidores.
- Inyecta:
  - IPs de los hipervisores.
  - Usuarios SSH.
  - Llaves privadas (desde archivos `.pem`).
  - Puertos TAP para conectividad de red.
  - MACs generadas deterministicamente.

### 7. **Orquestación del Patrón Saga**
- Publica eventos de despliegue en NATS JetStream.
- Inicia la cadena de microservicios:
  1. Slice Manager → `slice.deploy` (NATS).
  2. Queue Manager escucha y desglosa.
  3. Compute Provisioner y Network Orchestrator actúan en paralelo.
- Escucha `slice.result` para capturar la respuesta final.

### 8. **Protección contra Picos de Carga**
- Implementa una **cola interna** (`asyncio.Queue`) que procesa despliegues **secuencialmente**.
- Previene bloqueos en el worker si hay múltiples solicitudes simultáneas.
- Responde `202 Accepted` al usuario inmediatamente.

### 9. **Gestión de Borradores (Drafts)**
- Permite al usuario guardar una topología sin desplegar.
- Los borradores se almacenan en `SliceState.DRAFT`.
- Se pueden editar y desplegar después.

### 10. **Destrucción de Infraestructura**
- Endpoint `DELETE /slices/{slice_id}` para liberar recursos.
- Publica evento `slice.destroy` en NATS.
- Marca el slice como `TERMINATED`.

---

## 📁 Estructura del Módulo

```
SliceManager/
│
├── 📄 README.md                          # Este documento
├── 📄 requirements.txt                   # Dependencias de Python
├── 📄 docker-compose.yml                 # Configuración Docker Compose
├── 📄 Dockerfile                         # Imagen Docker del microservicio
│
└── 📁 app/                               # Código fuente principal
    ├── __init__.py                       # Inicializador del paquete
    ├── main.py                           # ⭐ Entry point de FastAPI + Worker Background
    ├── database.py                       # Configuración de SQLAlchemy
    ├── models.py                         # Definición de ORM (User, Slice, SliceState)
    ├── schemas.py                        # Modelos Pydantic (Validación de API)
    ├── utils.py                          # Funciones auxiliares (parsing de topologías)
    ├── nats_producer.py                  # Cliente NATS JetStream (publicador)
    └── telemetry.py                      # Cliente de Prometheus (métricas en vivo)
│
└── 📁 keys/                              # Credenciales SSH (PEM)
    ├── worker1.pem
    ├── worker2.pem
    ├── worker3.pem
    └── worker4.pem
```

### Descripción de Archivos

| Archivo | Responsabilidad |
|---------|-----------------|
| **main.py** | Entrada FastAPI, endpoints (`/slices/draft`, `/slices/{id}/deploy`, `/slices/{id}` DELETE), worker de cola en background |
| **database.py** | Configuración SQLAlchemy, creación de engine, sesiones, conexión a MySQL/SQLite |
| **models.py** | Definición ORM: `User` (usuarios del sistema), `Slice` (topologías guardadas), `SliceState` (enum de estados) |
| **schemas.py** | Validación Pydantic: `DeployRequest`, `DraftSaveRequest` |
| **utils.py** | `extract_vms_for_placement()`, `validate_topology_graph()` |
| **nats_producer.py** | `NATSProducer` singleton, métodos `publish_deploy()`, `publish_destroy()` |
| **telemetry.py** | `get_real_worker_metrics()` que consulta Prometheus |
| **keys/** | Llaves SSH para acceso a servidores (montadas en Read-Only en Docker) |

---

## 🛠️ Stack Tecnológico

### Backend
- **Python 3.12** - Lenguaje principal.
- **FastAPI** (v0.111.0) - Framework web asincrónico.
- **Uvicorn** (v0.29.0) - Servidor ASGI.

### Persistencia
- **SQLAlchemy** (v2.0.30) - ORM relacional.
- **PyMySQL** (v1.1.0) - Driver MySQL/MariaDB.
- **SQLite** (fallback local) - Para desarrollo sin MySQL.

### Mensajería y Asincronía
- **NATS-py** (v2.6.0) - Cliente Python para NATS JetStream.
- **asyncio** - Concurrencia nativa de Python.

### HTTP y Redes
- **httpx** (v0.27.0) - Cliente HTTP asíncrono (hacia VM Placement y Prometheus).

### Seguridad
- **Cryptography** (v42.0.5) - Para encriptación y manejo de credenciales.
- **Pydantic** (v2.7.1) - Validación de esquemas.

### Containerización
- **Docker** & **Docker Compose** - Aislamiento y orquestación de servicios.

---

## 🔌 Endpoints de la API

### 1. `POST /slices/draft` - Guardar Borrador

**Descripción:** Guarda una topología en estado DRAFT sin provisionar infraestructura.

**Payload:**
```json
{
  "name": "Mi Red PUCP 2026",
  "topology_json": {
    "nodes": [
      {
        "id": "vm-web",
        "type": "vm",
        "vcpus": 2,
        "ram_mb": 2048,
        "disk_gb": 20
      },
      {
        "id": "vm-db",
        "type": "vm",
        "vcpus": 4,
        "ram_mb": 4096,
        "disk_gb": 50
      }
    ],
    "edges": [
      {
        "source": "vm-web",
        "target": "vm-db"
      }
    ]
  }
}
```

**Respuesta (201 Created):**
```json
{
  "message": "Borrador guardado",
  "slice_id": 1
}
```

**Estados Guardados:**
- `state`: `DRAFT`
- `availability_zone`: `pending`
- `ttl_hours`: No definido (undefined)

---

### 2. `POST /slices/{slice_id}/deploy` - Solicitar Despliegue

**Descripción:** Inicia el proceso de provisioning para un slice guardado. Responde inmediatamente con status `202 Accepted` y procesa en background.

**Ruta:** `POST /slices/1/deploy`

**Payload:**
```json
{
  "availability_zone": "zone-a",
  "ttl_hours": 4,
  "motivo": "Prueba de provisioning automático"
}
```

**Respuesta (202 Accepted):**
```json
{
  "status": "ACCEPTED",
  "message": "Solicitud encolada para validación de recursos."
}
```

**Transición de Estado:** `DRAFT` → `PENDING_APPROVAL` → (background) → `PROVISIONING` / `ACTIVE` / `FAILED`

---

### 3. `DELETE /slices/{slice_id}` - Destruir/Liberar Recursos

**Descripción:** Solicita la destrucción de una infraestructura desplegada, liberando todos los recursos.

**Ruta:** `DELETE /slices/1`

**Respuesta (202 Accepted):**
```json
{
  "status": "ACCEPTED",
  "message": "Orden de destrucción enviada exitosamente a la cola."
}
```

**Validaciones:**
- No permite destruir slices en estado `DRAFT` o `TERMINATED`.
- Transición final: `TERMINATED`.

---

## ⚙️ Flujo de Datos Detallado

### Flujo de Despliegue Completo

```
┌──────────────────────────────────────────────────────────────────────┐
│ 1. CLIENTE (Frontend)                                                │
│    POST /slices/1/deploy { zone, ttl_hours, motivo }                 │
└────────────────────────────┬─────────────────────────────────────────┘
                             │
                             ▼
         ┌───────────────────────────────────────────┐
         │ 2. SLICE MANAGER (main.py)                │
         │    Endpoint: request_deploy()             │
         │    • Valida que el slice existe           │
         │    • Actualiza estado a PENDING_APPROVAL  │
         │    • Encolaa en placement_queue           │
         │    ✅ Responde 202 Accepted al cliente    │
         └───────────────────┬───────────────────────┘
                             │
                             ▼
         ┌───────────────────────────────────────────┐
         │ 3. BACKGROUND WORKER                      │
         │    (process_placement_worker)             │
         │    • Desencola desde asyncio.Queue        │
         │    • Recupera topology_json de BD         │
         └───────────────────┬───────────────────────┘
                             │
         ┌───────────────────▼───────────────────┐
         │ 4. VALIDACIÓN (FU-01)                 │
         │    validate_topology_graph()          │
         │    • Verifica nodos y edges           │
         │    • Si falla → SliceState.FAILED     │
         └───────────────────┬───────────────────┘
                             │
         ┌───────────────────▼───────────────────┐
         │ 5. EXTRACCIÓN DE RECURSOS             │
         │    extract_vms_for_placement()        │
         │    • Filtra nodos tipo "vm"           │
         │    • Formatea para VM Placement       │
         └───────────────────┬───────────────────┘
                             │
         ┌───────────────────▼──────────────────────────┐
         │ 6. CONSULTA DE TELEMETRÍA                    │
         │    (telemetry.py)                           │
         │    • HTTP GET a Prometheus                  │
         │    • Extrae CPU, RAM, Disk de workers       │
         │    • Con fallback a datos simulados         │
         └───────────────────┬──────────────────────────┘
                             │
         ┌───────────────────▼──────────────────────────┐
         │ 7. SOLICITUD A VM PLACEMENT                  │
         │    (HTTP POST)                              │
         │    Payload:                                 │
         │    {                                        │
         │      slice_id, zone,                        │
         │      vms: [ {vm_id, vcpus, ram, disk} ],   │
         │      workers: [ {worker_id, avail_*} ]     │
         │    }                                        │
         └───────────────────┬──────────────────────────┘
                             │
         ┌───────────────────▼──────────────────────────┐
         │ 8. RECIBE PLACEMENT_MAP                      │
         │    [                                        │
         │      { vm_id: "vm-web", worker_id: "srv-1"},│
         │      { vm_id: "vm-db", worker_id: "srv-2" } │
         │    ]                                        │
         └───────────────────┬──────────────────────────┘
                             │
         ┌───────────────────▼──────────────────────────┐
         │ 9. ENRIQUECIMIENTO DE CONTRATOS             │
         │    • Busca en server_inventory              │
         │    • Inyecta IPs, SSH users, keys           │
         │    • Genera TAPs y MACs                     │
         │    • Arma DeploySliceRequest                │
         └───────────────────┬──────────────────────────┘
                             │
         ┌───────────────────▼──────────────────────────┐
         │ 10. PUBLICACIÓN EN NATS                      │
         │     (nats_producer.publish_deploy)          │
         │     • Sujeto: "slice.deploy"                │
         │     • Canal: JetStream                      │
         │     • Actualiza estado a PROVISIONING       │
         └───────────────────┬──────────────────────────┘
                             │
                             ▼
         ┌──────────────────────────────────────────┐
         │ 11. QUEUE MANAGER RECIBE                 │
         │     (Escucha "slice.deploy")             │
         │     • Desglosa la tarea                  │
         │     • Enruta a Provisioners              │
         └──────────────────┬───────────────────────┘
                            │
            ┌───────────────┴───────────────┐
            │                               │
            ▼                               ▼
    ┌──────────────────┐          ┌──────────────────┐
    │ COMPUTE          │          │ NETWORK          │
    │ PROVISIONER      │          │ ORCHESTRATOR     │
    │ • Crea VMs       │          │ • Crea TAPs      │
    │ • Arranca QEMU   │          │ • Configura      │
    │ • VNC ports      │          │   bridges        │
    └────────┬─────────┘          └────────┬─────────┘
             │                             │
             └──────────────┬──────────────┘
                            │
                            ▼
         ┌──────────────────────────────────┐
         │ 12. ÉXITO / FALLO                │
         │     Publica en "slice.result"    │
         │     { slice_id, status }         │
         └──────────────────┬───────────────┘
                            │
                            ▼
         ┌──────────────────────────────────┐
         │ 13. SLICE MANAGER LISTENER       │
         │     (nats_result_listener)       │
         │     • Recibe en "slice.result"   │
         │     • Actualiza BD               │
         │     • Estado: ACTIVE o FAILED    │
         └──────────────────────────────────┘
```

---

## 💾 Modelos de Datos (ORM)

### User

```python
class User(Base):
    __tablename__ = "users"
    
    id: int (PK)                           # ID único del usuario
    username: str (UNIQUE)                 # Nombre único para login
    email: str (UNIQUE)                    # Email único
    is_approved: bool = False              # Flujo de aprobación (REQ-US-01)
    
    # Relación
    slices: List[Slice]                    # Usuario puede tener muchos Slices
```

**Ejemplo:**
```sql
INSERT INTO users (username, email, is_approved)
VALUES ('alex.torres', 'alex@pucp.edu.pe', TRUE);
```

---

### Slice

```python
class Slice(Base):
    __tablename__ = "slices"
    
    id: int (PK)                           # ID único del slice
    name: str                              # Nombre descriptivo (ej: "Mi Red PUCP")
    owner_id: int (FK → users.id)         # Propietario del slice
    state: SliceState (Enum)              # Estado actual (DRAFT, ACTIVE, etc)
    
    # Tiempo de vida
    ttl_hours: int = 4                     # Time-to-live en horas (REQ-US-08)
    availability_zone: str                 # Zona geográfica de despliegue
    
    # Configuración
    topology_json: JSON                    # JSON del lienzo guardado (REQ-US-07)
    
    # Relación
    owner: User                            # Propietario del slice
```

**Ejemplo:**
```sql
INSERT INTO slices (name, owner_id, state, ttl_hours, availability_zone, topology_json)
VALUES (
  'Mi Red PUCP',
  1,
  'draft',
  4,
  'zone-a',
  '{"nodes": [...], "edges": [...]}'
);
```

---

### SliceState

```python
class SliceState(str, enum.Enum):
    DRAFT             = "draft"             # Guardado sin desplegar
    PENDING_APPROVAL  = "pending_approval"  # Esperando aprobación
    PROVISIONING      = "provisioning"      # En proceso de aprovisionamiento
    ACTIVE            = "active"            # Ejecutándose sin problemas
    FAILED            = "failed"            # Error en provisioning
    TERMINATED        = "terminated"        # Destruido y liberado
```

---

## 📋 Esquemas Pydantic

### DeployRequest

```python
class DeployRequest(BaseModel):
    availability_zone: str    # Zona (ej: "zone-a", "zone-b")
    ttl_hours: int           # Tiempo de vida en horas
    motivo: str              # Razón del despliegue (campo textual)
```

**Ejemplo:**
```json
{
  "availability_zone": "zone-a",
  "ttl_hours": 4,
  "motivo": "Prueba de arquitectura de microservicios"
}
```

---

### DraftSaveRequest

```python
class DraftSaveRequest(BaseModel):
    name: str                # Nombre del slice
    topology_json: Dict[str, Any]  # JSON libre del lienzo
```

**Ejemplo:**
```json
{
  "name": "Mi Topología",
  "topology_json": {
    "nodes": [
      {"id": "vm-1", "type": "vm", "vcpus": 2, "ram_mb": 2048, "disk_gb": 20}
    ],
    "edges": []
  }
}
```

---

## 🔧 Configuración y Variables de Entorno

### Variables de Entorno Principales

| Variable | Default | Descripción | Archivo |
|----------|---------|-------------|---------|
| `NATS_URL` | `nats://localhost:4222` | URL del servidor NATS | `nats_producer.py` |
| `VM_PLACEMENT_URL` | `http://vm-placement:8080/placement` | Endpoint de VM Placement | `main.py` |
| `PROMETHEUS_URL` | `http://localhost:9090` | Endpoint de Prometheus | `telemetry.py` |
| `DB_USER` | `root` | Usuario de MySQL | `database.py` |
| `DB_PASSWORD` | (none) | Contraseña de MySQL | `database.py` |
| `DB_HOST` | `localhost` | Host de MySQL | `database.py` |
| `DB_NAME` | `pucp_cloud_db` | Nombre de BD | `database.py` |

### Archivo `.env` Recomendado

```env
# NATS
NATS_URL=nats://nats:4222

# VM Placement
VM_PLACEMENT_URL=http://vm-placement:8080/placement

# Prometheus (Telemetría)
PROMETHEUS_URL=http://prometheus:9090

# Base de Datos MySQL
DB_USER=cloud_admin
DB_PASSWORD=SecurePassword123
DB_HOST=mysql-db
DB_NAME=pucp_cloud_db
```

---

## 🚀 Instalación y Despliegue

### Requisitos Previos

- **Docker** & **Docker Compose** (recomendado)
- O **Python 3.12+** + pip (para desarrollo local)
- **Red Docker compartida** (si usas múltiples contenedores)

### Opción 1: Despliegue con Docker Compose (Recomendado)

```bash
# 1. Navegar al directorio del módulo
cd SliceManager

# 2. Crear la red compartida (si no existe)
docker network create pucp_cloud_net

# 3. Levantar el servicio
docker compose up --build -d

# 4. Verificar que esté corriendo
docker logs -f slice-manager

# 5. Acceder a Swagger (API docs)
# Abre en navegador: http://localhost:8000/docs
```

**Archivo `docker-compose.yml`:**
```yaml
services:
  slice-manager:
    build: .
    container_name: slice-manager
    restart: unless-stopped
    ports:
      - "8000:8000"
    environment:
      - NATS_URL=nats://nats:4222
      - VM_PLACEMENT_URL=http://vm-placement:8080/placement
      - PROMETHEUS_URL=http://prometheus:9090
    volumes:
      - ./keys:/app/keys:ro
    networks:
      - pucp_cloud_net
```

---

### Opción 2: Desarrollo Local (Sin Docker)

```bash
# 1. Crear entorno virtual
python -m venv venv

# 2. Activar entorno
# En Windows:
venv\Scripts\activate
# En Linux/Mac:
source venv/bin/activate

# 3. Instalar dependencias
pip install -r requirements.txt

# 4. Ejecutar servidor en modo desarrollo (con hot-reload)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 5. Acceder a Swagger
# http://localhost:8000/docs
```

---

### Opción 3: Ejecutar como Servicio Systemd (Producción Linux)

```bash
# Crear archivo de servicio
sudo nano /etc/systemd/system/slice-manager.service

# Contenido:
[Unit]
Description=Slice Manager - Cloud Orchestrator
After=network.target

[Service]
Type=notify
User=cloud-user
WorkingDirectory=/opt/slice-manager
Environment="PATH=/opt/slice-manager/venv/bin"
ExecStart=/opt/slice-manager/venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
Restart=always

[Install]
WantedBy=multi-user.target

# Habilitar y iniciar
sudo systemctl daemon-reload
sudo systemctl enable slice-manager
sudo systemctl start slice-manager
```

---

## 🔄 Cómo Funciona Internamente

### 1. Inicialización (Lifespan)

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    # AL INICIAR
    await nats_producer.connect()  # Conecta a NATS (localhost:4222)
    
    # Lanza worker de placement como tarea en background
    placement_task = asyncio.create_task(process_placement_worker())
    # Lanza listener de resultados
    result_task = asyncio.create_task(nats_result_listener())
    
    yield  # La app está viva
    
    # AL APAGAR
    placement_task.cancel()
    result_task.cancel()
    await nats_producer.disconnect()
```

**Lo que sucede:**
- Al iniciar el app, se conecta automáticamente a NATS.
- Lanza dos tareas en background que corren indefinidamente:
  1. **process_placement_worker**: Procesa la cola de despliegues.
  2. **nats_result_listener**: Escucha respuestas de NATS.
- Al apagar, termina limpiamente ambas tareas.

---

### 2. Procesamiento de Despliegue (Worker)

El `process_placement_worker()` es un **loop infinito** que:

```python
async def process_placement_worker():
    db = get_db_session()
    
    while True:
        # 1. Espera a que haya algo en la cola (BLOQUEANTE)
        request_data = await placement_queue.get()
        slice_id = request_data["slice_id"]
        zone = request_data["zone"]
        
        try:
            # 2. Recupera el slice de BD
            db_slice = db.query(Slice).filter(Slice.id == slice_id).first()
            
            # 3. Valida topología
            if not validate_topology_graph(db_slice.topology_json):
                db_slice.state = SliceState.FAILED
                db.commit()
                continue
            
            # 4. Extrae VMs
            dynamic_vms = extract_vms_for_placement(db_slice.topology_json)
            
            # 5. Consulta Prometheus
            real_workers_metrics = await get_real_worker_metrics()
            
            # 6. Envía a VM Placement
            response = await client.post(VM_PLACEMENT_URL, json={
                "slice_id": str(slice_id),
                "availability_zone": zone,
                "vms": dynamic_vms,
                "workers": real_workers_metrics
            })
            
            placement_result = response.json()
            
            # 7. Si es exitoso, enriquece y publica en NATS
            if placement_result.get("status") == "SUCCESS":
                placement_map = placement_result.get("placement_map")
                
                # Arma DeploySliceRequest con TAPs, MACs, SSH keys
                deploy_payload = {
                    "slice_id": str(slice_id),
                    "vms": [...],  # Enriquecidas con credenciales
                    "links": [...]  # Conexiones de red
                }
                
                # Publica en NATS
                await nats_producer.publish_deploy(deploy_payload)
                db_slice.state = SliceState.PROVISIONING
            else:
                db_slice.state = SliceState.FAILED
                
            db.commit()
            
        except Exception as e:
            logger.error(f"Error: {e}")
        finally:
            placement_queue.task_done()
```

**Puntos clave:**
- Es **secuencial**: Procesa un slice a la vez.
- Es **resiliente**: Captura excepciones sin romper el loop.
- Es **asíncrono**: Usa `await` para no bloquear otros endpoints.
- Respeta **transiciones de estado** en BD.

---

## 🔗 Integración con NATS

### ¿Qué es NATS?

NATS es un servidor de mensajería de **bajo acoplamiento** que permite que microservicios se comuniquen de forma **asíncrona** sin conocerse directamente.

### Cómo lo Usa Slice Manager

**Publicación (Slice Manager → NATS):**

```python
# En main.py, después de obtener el placement_map
deploy_payload = {
    "slice_id": "123",
    "request_id": "req-abc123",
    "vms": [
        {
            "vm_id": "vm-web",
            "worker_ip": "10.0.10.1",
            "ssh_user": "ubuntu",
            "ssh_private_key": "-----BEGIN...",
            "vcpus": 2,
            "ram_mb": 2048,
            "image_name": "ubuntu-22.04.qcow2",
            "tap_interfaces": [
                {"tap_name": "tap-vm-web-0", "mac": "52:54:00:ab:cd:ef"}
            ]
        }
    ],
    "links": [...]
}

# Publica
await nats_producer.publish_deploy(deploy_payload)
```

**Suscripción (NATS → Slice Manager):**

```python
async def nats_result_listener():
    # Se suscribe al canal "slice.result"
    async def message_handler(msg):
        data = json.loads(msg.data.decode())
        slice_id = int(data.get("slice_id"))
        status = data.get("status")  # "success" o "failure"
        
        # Actualiza BD
        db_slice = db.query(Slice).filter(Slice.id == slice_id).first()
        if status == "success":
            db_slice.state = SliceState.ACTIVE
        else:
            db_slice.state = SliceState.FAILED
        db.commit()
    
    await nats_producer.nc.subscribe("slice.result", cb=message_handler)
```

---

## 🎯 Integración con VM Placement

### Flujo

```
Slice Manager → HTTP POST → VM Placement → Decisión de Mapping
                                           (Round Robin / Bin Packing)
```

### Request (Slice Manager → VM Placement)

```json
{
  "slice_id": "123",
  "availability_zone": "zone-a",
  "vms": [
    {
      "vm_id": "vm-web",
      "vcpus": 2,
      "ram_mb": 2048,
      "disk_gb": 20
    }
  ],
  "workers": [
    {
      "worker_id": "server-1",
      "available_vcpus": 16,
      "available_ram_mb": 32000,
      "available_disk_gb": 500
    }
  ]
}
```

### Response (VM Placement → Slice Manager)

```json
{
  "status": "SUCCESS",
  "placement_map": [
    {
      "vm_id": "vm-web",
      "worker_id": "server-1"
    }
  ]
}
```

---

## 📊 Integración con Telemetría (Prometheus)

### ¿Qué Métricas Extrae?

```python
# En telemetry.py
async def get_real_worker_metrics():
    # Query 1: RAM disponible
    ram = await fetch_metric("node_memory_MemAvailable_bytes/1024/1024")
    
    # Query 2: CPUs libres
    cpus = await fetch_metric('count by(instance)(node_cpu_seconds_total{mode="idle"})')
    
    # Query 3: Espacio en disco
    disk = await fetch_metric('node_filesystem_avail_bytes{mountpoint="/"}/1024/1024/1024')
    
    return [
        {
            "worker_id": "server-1",
            "available_vcpus": 10,
            "available_ram_mb": 16000,
            "available_disk_gb": 500
        }
    ]
```

### Fallback Automático

Si Prometheus falla (red caída, servicio offline), el módulo **no falla**:

```python
except Exception as e:
    logger.error(f"Falló conexión con Prometheus: {e}")
    logger.warning("Usando métricas simuladas...")
    return [
        {"worker_id": "server-1", "available_vcpus": 10, ...},
        {"worker_id": "server-2", "available_vcpus": 10, ...}
    ]
```

---

## 🔐 Seguridad y Manejo de Credenciales

### Almacenamiento de Llaves SSH

Las llaves privadas se almacenan en archivos `.pem` **en la carpeta `keys/`** y se **montan en Read-Only** en el contenedor Docker:

```yaml
volumes:
  - ./keys:/app/keys:ro  # :ro = Read-Only
```

**Archivo: `keys/worker1.pem`**
```
-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA4+P3...
...
-----END RSA PRIVATE KEY-----
```

### Lectura de Llaves en Runtime

```python
def get_ssh_key(filepath: str) -> str:
    if not os.path.exists(filepath):
        logger.error(f"Llave no encontrada: {filepath}")
        return ""
    with open(filepath, "r") as key_file:
        return key_file.read()

# Uso
ssh_key = get_ssh_key("keys/worker1.pem")
```

### Inventario de Servidores

```python
server_inventory = {
    "server-1": {"ip": "10.0.10.1", "user": "ubuntu", "key": "..."},
    "server-2": {"ip": "10.0.10.2", "user": "ubuntu", "key": "..."},
    "server-3": {"ip": "10.0.10.3", "user": "ubuntu", "key": "..."},
    "server-4": {"ip": "10.0.10.4", "user": "ubuntu", "key": "..."}
}
```

### Generación Determinista de MACs

Las direcciones MAC se generan usando **hash SHA256 del slice_id**:

```python
slice_hash = hashlib.sha256(str(slice_id).encode()).hexdigest()
mac_prefix = f"52:54:00:{slice_hash[:2]}:{slice_hash[2:4]}"
# Ejemplo: 52:54:00:ab:cd (siempre igual para el mismo slice_id)

# Luego incrementa el contador global
mac1 = f"{mac_prefix}:00:00".upper()  # 52:54:00:AB:CD:00:00
mac2 = f"{mac_prefix}:00:01".upper()  # 52:54:00:AB:CD:00:01
```

**Ventaja:** Las MACs son **reproducibles** y **únicas** por slice.

---

## ⚠️ Manejo de Errores y Tolerancia a Fallos

### Escenarios de Error Manejados

| Escenario | Acción |
|-----------|--------|
| Slice no encontrado | HTTP 404 |
| Topología inválida | Marcar como `FAILED`, continuar |
| Prometheus offline | Usar métricas simuladas, continuar |
| VM Placement no responde | HTTP timeout, marcar como `FAILED` |
| NATS no disponible | Log de error, reintento en próxima iteración |
| Fallo SSH al leer llaves | Log de error, proseguir sin esa llave |
| Excepción en worker | Catch + log, task_done(), sigue el loop |

### Reintentos

Actualmente **no hay reintentos automáticos**, pero se pueden agregar con:

```python
from tenacity import retry, stop_after_attempt, wait_exponential

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10)
)
async def publish_with_retry(payload):
    await nats_producer.publish_deploy(payload)
```

---

## 🗺️ Roadmap y Mejoras Futuras

### Fase 1: Completitud Actual ✅
- [x] API Gateway (FastAPI)
- [x] Persistencia (SQLAlchemy + MySQL/SQLite)
- [x] Queue en background (asyncio)
- [x] Integración NATS (Publicador)
- [x] Integración VM Placement
- [x] Consulta de Telemetría
- [x] Enriquecimiento de contratos

### Fase 2: Listener de Resultados 🔄
- [ ] Implementar `nats_result_listener` completo (parcialmente hecho).
- [ ] Actualización de estado desde NATS hacia BD.
- [ ] Notificaciones al frontend (WebSocket o polling).

### Fase 3: Extracción Avanzada de Topologías 📡
- [ ] Parseo completo de **edges** (conexiones de red).
- [ ] Cálculo de **VLAN IDs** automático.
- [ ] Soporte para **Security Groups** / **Firewall Rules**.
- [ ] Validaciones de topología más complejas.

### Fase 4: Seguridad 🔒
- [ ] Autenticación JWT integrada.
- [ ] Extracción de `user_id` desde token.
- [ ] Rate limiting por usuario.
- [ ] Validación de permisos (REQ-US-01 flujo de aprobación).
- [ ] Encriptación de credenciales en BD.
- [ ] Rotation de llaves SSH.

### Fase 5: Monitoreo y Observabilidad 📈
- [ ] Métricas Prometheus (latencia, throughput, errores).
- [ ] Trazas distribuidas (OpenTelemetry).
- [ ] Alertas automáticas en caso de fallos.
- [ ] Dashboard Grafana.

### Fase 6: Resiliencia 💪
- [ ] Reintentos automáticos con backoff exponencial.
- [ ] Circuit breaker para VM Placement.
- [ ] Replicación de estado (HA).
- [ ] Failover automático.

### Fase 7: Escalabilidad 📈
- [ ] Múltiples workers (sharding de cola).
- [ ] Caché de decisiones de placement.
- [ ] Pool de conexiones a NATS y Prometheus.
- [ ] Compresión de payloads NATS.

---

## 📚 Documentación Adicional

### Ubicaciones de Archivos Clave

- **Código principal:** `SliceManager/app/main.py`
- **Modelos ORM:** `SliceManager/app/models.py`
- **Esquemas API:** `SliceManager/app/schemas.py`
- **Cliente NATS:** `SliceManager/app/nats_producer.py`
- **Telemetría:** `SliceManager/app/telemetry.py`
- **Utilidades:** `SliceManager/app/utils.py`

### Dependencias Críticas

```
fastapi==0.111.0          # Framework web
uvicorn==0.29.0           # Servidor ASGI
sqlalchemy==2.0.30        # ORM
pymysql==1.1.0            # Driver MySQL
nats-py==2.6.0            # Cliente NATS
httpx==0.27.0             # Cliente HTTP async
pydantic==2.7.1           # Validación de datos
cryptography==42.0.5      # Encriptación
```

### Comandos Útiles

```bash
# Ver logs en tiempo real
docker logs -f slice-manager

# Entrar al contenedor
docker exec -it slice-manager bash

# Detener el servicio
docker compose down

# Eliminar datos persistentes
docker compose down -v

# Reconstruir imagen
docker compose build --no-cache

# Ejecutar pruebas unitarias (si las hay)
pytest app/tests -v

# Verificar sintaxis Python
python -m py_compile app/**/*.py
```

---

## 🤝 Contribuciones

Para contribuir al módulo de Slice Manager:

1. Fork el repositorio.
2. Crea una rama (`git checkout -b feature/mi-feature`).
3. Haz commit de los cambios (`git commit -am 'Agrego feature'`).
4. Push a la rama (`git push origin feature/mi-feature`).
5. Abre un Pull Request.

---

## 📝 Licencia

Este proyecto es parte de **Cloud-G10-26.1**, un proyecto académico de PUCP (Pontificia Universidad Católica del Perú).

---

## 👥 Autores

- **Equipo Cloud-G10-26.1** - Desarrollo y mantenimiento.
- **Alex Torres** - Contribuciones iniciales.
- **Resto del equipo PUCP** - Revisión y feedback.

---

## 📞 Soporte y Contacto

Para reportar bugs o solicitar features, abre un issue en el repositorio o contacta al equipo de desarrollo.

**Correo:** cloud-team@pucp.edu.pe  
**Slack:** #cloud-g10-26-1

---

**Última actualización:** Abril 2026
