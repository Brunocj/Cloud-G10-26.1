"""
Router: /auth/** → Keycloak

Ruta pública — sin validación JWT.
El cliente (Web App) manda credenciales, Keycloak responde con el token JWT.
Este router es un proxy transparente hacia Keycloak.

Estará en 503 hasta que el contenedor de Keycloak esté levantado —
eso es comportamiento esperado, no un error del gateway.
"""

import logging

from starlette.requests import Request
from starlette.responses import Response

from app.config import settings
from app.proxy import forward

logger = logging.getLogger("api-gateway.identity")


async def forward_identity(request: Request) -> Response:
    path = request.path_params.get("path", "")
    target_url = f"{settings.KEYCLOAK_URL}/auth/{path}"
    return await forward(request, target_url)
