# Keycloak — PUCP Cloud Orchestrator

Servicio de Identity & Access Management basado en **Keycloak 24**.

## Estructura

```
Keycloak/
├── docker-compose.yml    # Servicio Keycloak (puerto 8086)
├── realm-export.json     # Realm preconfigurado (importado al arrancar)
├── verify_keycloak.py    # Script de verificación post-arranque
└── README.md
```

## Arrancar Keycloak

```bash
cd Keycloak/
docker compose up -d
```

> El primer arranque tarda ~60 segundos mientras Keycloak importa el realm.

## Consola de Administración

| Campo    | Valor                        |
|----------|------------------------------|
| URL      | http://localhost:8086        |
| Usuario  | `admin`                      |
| Password | `admin123`                   |
| Realm    | `pucp-cloud`                 |

## Realm: `pucp-cloud`

### Roles Globales

Los roles deben coincidir exactamente con los que espera el API Gateway en `realm_access.roles`.
El Gateway extrae el rol de mayor prioridad y lo inyecta como `X-User-Role` hacia el Slice Manager.

| Rol | Prioridad | Descripción |
|-----|-----------|-------------|
| `superAdmin` | 4 | Superadministrador — acceso total |
| `admin` | 3 | Administrador de plataforma — acceso total |
| `jefeProyecto` | 2 | Docente / jefe de proyecto — gestiona slices de su grupo |
| `usuario` | 1 | Estudiante — gestiona sus propios slices e imágenes |

### Usuarios de Prueba

| Username         | Password      | Rol           | Email                  |
|------------------|---------------|---------------|------------------------|
| `admin.pucp`     | `Admin123!`   | admin         | admin@pucp.edu.pe      |
| `prof.garcia`    | `Prof123!`    | jefeProyecto  | garcia@pucp.edu.pe     |
| `alumno.lopez`   | `Alumno123!`  | usuario       | lopez@pucp.edu.pe      |
| `alumno.torres`  | `Alumno123!`  | usuario       | torres@pucp.edu.pe     |

### Clientes OIDC

| Client ID            | Tipo       | Uso                                          |
|----------------------|------------|----------------------------------------------|
| `pucp-cloud-webapp`  | Público    | SPA React — Login con PKCE                   |
| `pucp-cloud-api`     | Bearer-only| API Gateway — sólo verifica tokens entrantes |

## Verificar que todo funciona

```bash
cd Keycloak/
pip install requests
python verify_keycloak.py
```

## Endpoints Importantes para el API Gateway

```
JWKS (clave pública):
  http://keycloak:8080/realms/pucp-cloud/protocol/openid-connect/certs

Issuer:
  http://keycloak:8080/realms/pucp-cloud

Token (para pruebas con curl):
  http://localhost:8086/realms/pucp-cloud/protocol/openid-connect/token
```

### Obtener un token de prueba con `curl`

```bash
curl -s -X POST \
  "http://localhost:8086/realms/pucp-cloud/protocol/openid-connect/token" \
  -d "grant_type=password" \
  -d "client_id=pucp-cloud-webapp" \
  -d "username=alumno.lopez" \
  -d "password=Alumno123!" | python -m json.tool
```

## Variables de Entorno para el API Gateway

Cuando Keycloak esté corriendo, actualiza el `docker-compose.yml` del **ApiGW**:

```yaml
JWT_ENABLED:   "true"
JWT_JWKS_URL:  "http://keycloak:8080/realms/pucp-cloud/protocol/openid-connect/certs"
JWT_ISSUER:    "http://keycloak:8080/realms/pucp-cloud"
JWT_AUDIENCE:  "account"
```

> **Nota**: Desde dentro de Docker, usa `keycloak:8080` (nombre de servicio + puerto interno).
> Desde el host/browser, usa `localhost:8086`.

## Red Docker

El contenedor se conecta a la red externa `pucp_cloud_net`.  
Asegúrate de que la red esté creada antes de arrancar:

```bash
docker network create pucp_cloud_net
```
