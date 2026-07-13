# Web App — PUCP Cloud Orchestrator

Interfaz de usuario del sistema de orquestación de slices. Desarrollada con **React + Vite**.
Permite a los usuarios crear topologías de red de forma visual (canvas drag-and-drop),
desplegar slices, monitorizar el estado de las VMs y acceder a las consolas VNC.

---

## Funcionalidades principales

- **Canvas interactivo**: creación de topologías arrastrando nodos (VMs) y enlaces,
  incluyendo **plantillas predefinidas** (lineal, malla, árbol, anillo, bus) y su combinación.
  Configuración por VM: recursos (vCPU/RAM/disco), **flavor**, imagen, credenciales,
  acceso a internet + IP externa, y **reglas de firewall** (security rules).
- **Selección de zona de disponibilidad** (Linux Cluster u OpenStack) al desplegar,
  con selector de **TTL** y motivo — la interfaz es la misma para ambas zonas (agnóstica).
- **Flavors** (panel lateral): crear/listar/borrar plantillas de recursos con
  visibilidad global/privado/proyecto.
- **Plantillas de topología**: publicar un slice como plantilla y crear nuevos slices a partir de ella.
- **Modo Edición**: editar slices ya desplegados (agregar/quitar VMs y enlaces en caliente).
- **Autenticación**: login con Keycloak (PKCE). El JWT se guarda en memoria y se envía
  al API Gateway; la UI se adapta al **rol** (RBAC: usuario / jefeProyecto / admin / superAdmin).
- **Gestión de slices**: borradores, deploy, destroy, listado con estado en **tiempo real** (WebSockets).
- **Consola VNC**: consola de cada VM en el browser (túnel SSH en Linux, token noVNC en OpenStack).
- **Gestión de imágenes**: subir/listar/eliminar (`.qcow2`/`.img`/`.iso`) — NFS local + Glance.
- **Vistas de administración**: consumo de recursos por proyecto, monitor de infraestructura,
  **logs por slice (Loki)**, bitácora de auditoría, gestión de usuarios/proyectos y aprobaciones.
- **Perfil**: datos del usuario, rol y llave SSH pública.

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
