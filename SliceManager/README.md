# Slice Manager (API Gateway & Main Orchestrator)

Microservicio principal del ecosistema de orquestación en la nube. Actúa como el **API Gateway** para el frontend, gestiona el estado de los recursos en la base de datos y actúa como el **Iniciador del Patrón Saga**, traduciendo topologías abstractas de red en comandos físicos para la infraestructura.

---

## 🏛 Rol en la Arquitectura

El Slice Manager es el puente entre las intenciones del usuario (dibujos en el canvas) y la ejecución física en los servidores (workers). Implementa una arquitectura híbrida de comunicación:

- **HTTP/REST (Síncrono):** Para interactuar con el Frontend, Telemetría (Prometheus) y el motor de VM Placement.
- **NATS JetStream (Asíncrono / Event-Driven):** Para delegar la ejecución física al Queue Manager sin bloquear el hilo principal.

```text
[Frontend (Canvas)]
       │ (HTTP POST)
       ▼
[ Slice Manager ] ──(HTTP GET)──► [ Prometheus (Telemetría viva) ]
       │
       ├──(HTTP POST)──► [ VM Placement (Round Robin) ]
       │
       └──(NATS Publish)──► [ Queue Manager (Orquestación Física) ]
```

---

## 🎯 Responsabilidades

- **Gestión de Estado:** Almacena y actualiza el ciclo de vida de los Slices (`DRAFT` → `PENDING_APPROVAL` → `PROVISIONING` → `ACTIVE` / `FAILED`).
- **Extracción de Topologías:** Analiza el `topology_json` generado por el frontend para extraer recursos computacionales (VMs). *(Próximamente: extracción de enlaces de red).*
- **Monitoreo Dinámico:** Consulta la disponibilidad real de CPU, RAM y Disco de los hipervisores antes de cada despliegue.
- **Enriquecimiento de Contratos:** Cruza la decisión matemática del `VM Placement` con el inventario de infraestructura para inyectar IPs, usuarios y llaves SSH.
- **Protección de Tráfico:** Implementa un Worker en segundo plano con una cola interna (`asyncio.Queue`) para procesar despliegues sin colapsar ante picos de peticiones.

---

## 🛣 Endpoints Principales

### `POST /slices/draft`

Recibe el diseño abstracto del frontend y lo guarda en base de datos.

**Payload:**

```json
{
  "name": "Mi Topología PUCP",
  "topology_json": {
    "nodes": [
      { "id": "vm-app1", "type": "vm", "vcpus": 2, "ram_mb": 2048, "disk_gb": 20 }
    ],
    "edges": []
  }
}
```

**Respuesta `201 Created`:** Retorna el ID del slice generado (ej. `1`) y estado `DRAFT`.

---

### `POST /slices/{slice_id}/deploy`

Inicia la orquestación. Responde inmediatamente al usuario mientras procesa en segundo plano.

**Respuesta `202 Accepted`:**

```json
{ "message": "Deployment en proceso" }
```

---

## ⚙️ Flujo Interno del Worker (Background Process)

Cuando un Slice entra a la cola de despliegue, el `process_placement_worker` ejecuta la siguiente coreografía:

1. **Extracción:** Filtra el `topology_json` buscando nodos de tipo `vm`.
2. **Telemetría:** Invoca a `app/telemetry.py` para consultar a Prometheus (vía un túnel SSH o local) los recursos disponibles en los workers. Tiene un *fallback* automático a datos simulados si Prometheus está caído.
3. **Decisión de Placement:** Envía los requerimientos y los recursos disponibles al `VM Placement` por HTTP y recibe el mapeo exacto (ej. `vm-app1 → server-1`).
4. **Transformación:** Busca en su diccionario de infraestructura (`server_inventory`) las credenciales SSH e IP de `server-1`.
5. **Delegación Física:** Empaqueta todo en el estricto contrato `DeploySliceRequest` y lo publica en NATS JetStream (canal `slice.deploy`).

---

## 🔧 Variables de Entorno (`.env`)

| Variable | Default | Descripción |
|---|---|---|
| `NATS_URL` | `nats://localhost:4222` | URL del servidor de mensajería NATS |
| `VM_PLACEMENT_URL` | `http://127.0.0.1:8080/placement` | Endpoint del algoritmo de Placement |
| `PROMETHEUS_URL` | `http://localhost:9090` | Endpoint de Telemetría (o `host.docker.internal` si usa túnel SSH) |

---

## 🐳 Despliegue con Docker

El Slice Manager debe correr en la red compartida (`pucp_cloud_net`) para poder descubrir y comunicarse con el resto de microservicios usando sus nombres de contenedor.

```bash
# 1. Asegurarse de que la red externa existe
docker network create pucp_cloud_net

# 2. Levantar el servicio en segundo plano
docker compose up --build -d

# 3. Ver los logs en tiempo real
docker logs -f slice-manager
```

### Ejecución Local (Desarrollo)

```bash
python -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

---

## 📂 Estructura del Código

```text
SliceManager/
├── app/
│   ├── main.py              # API FastAPI, Endpoints y Background Worker
│   ├── nats_producer.py     # Conexión JetStream y publicador (slice.deploy)
│   ├── telemetry.py         # Cliente HTTP asíncrono hacia Prometheus
│   └── utils.py             # Lógica de extracción de topologías (JSON parser)
├── Dockerfile               # Receta de construcción de imagen (Python 3.12-slim)
├── docker-compose.yml       # Orquestación del contenedor y conexión a red externa
└── requirements.txt         # Dependencias (FastAPI, NATS-py, httpx, etc.)
```

---

## 🚀 Próximos Pasos (Roadmap)

1. **NATS Consumer (Flujo de Vuelta):** Implementar la escucha del canal `slice.result` para capturar la respuesta del Queue Manager y actualizar el estado final en BD (`ACTIVE` o `FAILED`).
2. **Extracción de Redes (Edges):** Modificar `utils.py` para parsear las conexiones lógicas (cables) y agregarlas al payload de NATS para el futuro `Network Orchestrator`.
3. **Seguridad JWT:** Integrar dependencias para bloquear los endpoints y extraer el `user_id` real del token en lugar de usar datos quemados.
