"""
Middleware de autenticación JWT para el API Gateway.

Flujo por petición:
  1. Rutas públicas (/health, /auth/**, OPTIONS) → dejar pasar sin token.
  2. Extraer 'Authorization: Bearer <token>' del header.
  3. Validar firma RSA con la clave pública de Keycloak (JWKS).
  4. Verificar expiración, issuer y audience.
  5. Extraer sub (X-User-Id) y rol más alto (X-User-Role).
  6. Eliminar 'Authorization' del request y añadir los headers de identidad.
  7. Hacer pasar la petición mutada al router downstream.

NOTA: Se implementa como middleware ASGI puro (NO como BaseHTTPMiddleware).
BaseHTTPMiddleware envuelve el body del request en un stream intermedio para
poder reenviarlo tras ejecutar el dispatch, y ese wrapping puede truncar o
corromper uploads grandes (imágenes de varios GB hacia OpenStack Glance),
provocando que el body llegue incompleto al Slice Manager y su parser
multipart falle con 400 "There was an error parsing the body". Como este
middleware solo necesita leer headers (nunca el body), un ASGI puro que solo
muta `scope["headers"]` deja el stream original intacto para el resto de la
cadena.
"""
from __future__ import annotations

import logging

import jwt
from jwt import ExpiredSignatureError, InvalidTokenError, PyJWKClient
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import settings

logger = logging.getLogger("api-gateway.jwt")

# ── Rutas que NO requieren token ──────────────────────────────────────────────
_PUBLIC_EXACT = {
    "/health", "/docs", "/openapi.json", "/redoc",
    "/api/v1/users/register",   # auto-registro (REQ-US-01) — sin token
}
_PUBLIC_PREFIX = (
    "/auth/",   # proxy transparente hacia Keycloak
    "/vnc/",    # WebSocket VNC — protegido por túnel SSH, browsers no envían Bearer
)

# ── Orden de prioridad de roles (izq=menor, der=mayor) ───────────────────────
_ROLE_PRIORITY: list[str] = ["usuario", "jefeProyecto", "admin", "superAdmin"]


def _top_role(roles: list[str]) -> str | None:
    """Devuelve el rol de mayor prioridad encontrado en la lista."""
    known = [r for r in roles if r in _ROLE_PRIORITY]
    if not known:
        return None
    known.sort(key=lambda r: _ROLE_PRIORITY.index(r))
    return known[-1]   # el de mayor prioridad


def build_jwks_client() -> PyJWKClient:
    """
    Construye el cliente JWKS que descarga y cachea las claves públicas
    de Keycloak.  Se llama UNA vez en el lifespan del Gateway.
    """
    logger.info("Cargando JWKS desde %s", settings.JWT_JWKS_URL)
    # cache_keys=True → reutiliza la clave entre validaciones sin re-fetching
    # lifespan=3600   → refresca la clave cada hora
    return PyJWKClient(
        settings.JWT_JWKS_URL,
        cache_keys=True,
        lifespan=3600,
    )


class JWTAuthMiddleware:
    """
    Middleware ASGI puro que valida el token JWT de Keycloak y muta el scope
    añadiendo X-User-Id y X-User-Role antes de reenviarlo al upstream.

    No hereda de BaseHTTPMiddleware ni toca el body del request en ningún
    momento — solo lee headers y reescribe `scope["headers"]`.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # 1. Rutas públicas → pasar sin verificar
        path = scope["path"]
        if scope["method"] == "OPTIONS" or path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIX):
            await self.app(scope, receive, send)
            return

        jwks_client = scope["app"].state.jwks_client
        if jwks_client is None:
            # Keycloak no disponible al arrancar — rechazar todas las peticiones
            response = JSONResponse(
                {"detail": "Servicio de autenticación no disponible"},
                status_code=503,
            )
            await response(scope, receive, send)
            return

        # 2. Extraer token
        headers = Headers(scope=scope)
        auth_header = headers.get("authorization", "")
        if not auth_header.startswith("Bearer "):
            response = JSONResponse(
                {"detail": "Autenticación requerida. Incluye 'Authorization: Bearer <token>'."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer"},
            )
            await response(scope, receive, send)
            return

        token = auth_header.removeprefix("Bearer ").strip()

        # 3. Validar token
        payload = self._validate(jwks_client, token, path)
        if isinstance(payload, JSONResponse):
            await payload(scope, receive, send)
            return

        # 4. Extraer identidad
        user_id = payload.get("sub", "unknown")
        roles   = payload.get("realm_access", {}).get("roles", [])
        role    = _top_role(roles)

        if not role:
            logger.warning(
                "Usuario %s no tiene rol reconocido. Roles en token: %s",
                user_id[:8], roles,
            )
            response = JSONResponse(
                {"detail": "El usuario no tiene ningún rol autorizado en esta plataforma."},
                status_code=403,
            )
            await response(scope, receive, send)
            return

        # 5. Mutar el scope ASGI: inyectar X-User-* y eliminar Authorization
        self._mutate_headers(scope, user_id, role)

        logger.info(
            "JWT OK · user=%s… role=%s · %s %s",
            user_id[:8], role, scope["method"], path,
        )

        await self.app(scope, receive, send)

    # ── Helpers privados ──────────────────────────────────────────────────────

    def _validate(self, jwks_client: PyJWKClient, token: str, path: str):
        """
        Devuelve el payload decodificado o un JSONResponse de error.
        Separado del __call__ para mantenerlo legible.
        """
        try:
            signing_key = jwks_client.get_signing_key_from_jwt(token)

            decode_options: dict = {"verify_exp": True}
            decode_kwargs: dict = {
                "algorithms": ["RS256"],
                "issuer":     settings.JWT_ISSUER,
                "options":    decode_options,
            }

            # Audience es opcional: si está vacío, no verificamos
            if settings.JWT_AUDIENCE:
                decode_kwargs["audience"] = settings.JWT_AUDIENCE
            else:
                decode_options["verify_aud"] = False

            return jwt.decode(token, signing_key.key, **decode_kwargs)

        except ExpiredSignatureError:
            logger.warning("Token expirado · path=%s", path)
            return JSONResponse(
                {"detail": "El token ha expirado. Vuelve a iniciar sesión."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            )
        except InvalidTokenError as exc:
            logger.warning("Token inválido · %s · path=%s", exc, path)
            return JSONResponse(
                {"detail": "Token inválido."},
                status_code=401,
                headers={"WWW-Authenticate": "Bearer error=\"invalid_token\""},
            )
        except Exception as exc:
            logger.error("Error inesperado validando JWT: %s", exc, exc_info=True)
            return JSONResponse(
                {"detail": "Error interno al validar el token."},
                status_code=500,
            )

    @staticmethod
    def _mutate_headers(scope: Scope, user_id: str, role: str) -> None:
        """
        Modifica el scope ASGI para:
          • Añadir X-User-Id y X-User-Role
          • Eliminar Authorization (el Slice Manager no debe verlo)
        """
        # Copiar headers actuales eliminando Authorization
        new_headers = [
            (name, value)
            for name, value in scope["headers"]
            if name.lower() != b"authorization"
        ]
        # Añadir headers de identidad
        new_headers.append((b"x-user-id",   user_id.encode()))
        new_headers.append((b"x-user-role", role.encode()))

        scope["headers"] = new_headers
