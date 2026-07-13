# Ingeniería de Redes Cloud-G8-26.1

Plataforma cloud basada en microservicios que orquesta recursos sobre clústeres Linux y OpenStack.

📖 [English version below]

---

## 🇪🇸 Español

### Información general

Cada carpeta de este repositorio corresponde a un microservicio independiente del proyecto. Para su correcto funcionamiento se requiere una infraestructura física o virtual con la topología mostrada a continuación.

<img width="967" height="712" alt="image" src="https://github.com/user-attachments/assets/00c6f692-13db-4623-b8df-fe865b7d0f8c" />



### Microservicios

| Servicio | Descripción |
|---|---|
| **ApiGW** | API Gateway, punto de entrada del sistema |
| **Keycloak** | Autenticación y gestión de identidad |
| **WebApp** | Interfaz web para el usuario |
| **SliceManager** | Gestión y orquestación de slices |
| **VMPlacement** | Algoritmos de colocación de VMs |
| **ComputeProvisioner** | Aprovisionamiento de recursos de cómputo |
| **NetworkOrchestrator** | Orquestación de red (OVS, firewall, puertos) |
| **QueueManager** | Comunicación asíncrona entre servicios |
| **Observability** | Métricas, logs y monitoreo |

### Instalación

```bash
# 1. Clonar el repositorio
git clone https://github.com/Brunocj/Cloud-G8-26.1.git
cd Cloud-G8-26.1

# 2. Desplegar cada microservicio
docker compose up -d --build
```

Ejecuta `docker compose up -d --build` en cada carpeta de microservicio.

> **Nota:** Cada carpeta contiene su propio `README.md` con la descripción funcional y los endpoints del microservicio.

---

## 🇬🇧 English version

### Overview

Each folder in this repository is an independent microservice. Correct operation requires a physical or virtual infrastructure matching the topology shown above.



### Microservices

| Service | Description |
|---|---|
| **ApiGW** | API Gateway, system entry point |
| **Keycloak** | Authentication and identity management |
| **WebApp** | User-facing web interface |
| **SliceManager** | Slice management and orchestration |
| **VMPlacement** | VM placement algorithms |
| **ComputeProvisioner** | Compute resource provisioning |
| **NetworkOrchestrator** | Network orchestration (OVS, firewall, ports) |
| **QueueManager** | Async inter-service messaging |
| **Observability** | Metrics, logs and monitoring |

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/Brunocj/Cloud-G8-26.1.git
cd Cloud-G8-26.1

# 2. Deploy each microservice
docker compose up -d --build
```

Run `docker compose up -d --build` inside every microservice folder.

> **Note:** Each folder ships with its own `README.md` describing the service and its endpoints.
