# Web App — PUCP Cloud Orchestrator

Interfaz de usuario del sistema de orquestación de slices. Desarrollada con **React + Vite**.
Permite a los usuarios crear topologías de red de forma visual (canvas drag-and-drop),
desplegar slices, monitorizar el estado de las VMs y acceder a las consolas VNC.

---

## Funcionalidades principales

- **Canvas interactivo**: creación de topologías de red arrastrando nodos (VMs) y
  conectándolos con enlaces. Configuración de recursos (vCPU, RAM, disco) por VM.
- **Autenticación**: login con Keycloak (PKCE flow). El token JWT se almacena en memoria
  y se envía en cada request al API Gateway.
- **Gestión de slices**: crear borradores, desplegar, destruir y listar slices con su estado.
- **Consola VNC**: acceso a la consola de cada VM directamente desde el browser,
  usando WebSocket proxiado por el API Gateway (túnel SSH para Linux Cluster, token Nova para OpenStack).
- **Gestión de imágenes**: subir, listar y eliminar imágenes de disco (`.qcow2`, `.img`, `.iso`).
  Soporta imágenes en el NFS local y en OpenStack Glance.
- **Perfil de usuario**: visualización del rol y datos del usuario autenticado.

---

## Estructura del código

```
src/
├── App.jsx                  # Rutas principales y layout
├── main.jsx                 # Punto de entrada React
├── auth/                    # Login, logout, contexto de autenticación (Keycloak)
├── canvas/                  # Editor de topología drag-and-drop
├── components/
│   ├── sidebar/             # Barra lateral de navegación
│   ├── modals/              # Modales (deploy, configuración de VM, imágenes)
│   ├── ui/                  # Componentes reutilizables (botones, inputs, tablas)
│   └── profile/             # Vista de perfil de usuario
├── VmConsole.jsx            # Componente de consola VNC (WebSocket)
├── hooks/                   # Custom hooks (fetching, estado)
├── utils/                   # Utilidades (formateo, llamadas a la API)
└── theme/                   # Variables de estilo
```

---

## Levantar en desarrollo

```bash
cd WebApp/
npm install
npm run dev
```

La app estará disponible en `http://localhost:5173`.

Requiere que el API Gateway esté corriendo en `http://localhost:8085` (o configurar `VITE_API_URL`).

---

## Variables de entorno

Crear un archivo `.env.local` en `WebApp/`:

```env
VITE_API_URL=http://localhost:8085
VITE_KEYCLOAK_URL=http://localhost:8086
VITE_KEYCLOAK_REALM=pucp-cloud
VITE_KEYCLOAK_CLIENT_ID=pucp-cloud-webapp
```

---

## Build de producción

```bash
npm run build
```

Los archivos estáticos quedan en `WebApp/dist/` listos para servir.
