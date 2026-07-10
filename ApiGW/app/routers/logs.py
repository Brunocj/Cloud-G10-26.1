"""
Router de logs — expone (solo lectura) la API HTTP de Loki hacia la WebApp.

Acceso restringido al rol `superAdmin`. Loki no tiene autenticación propia y
queda encerrado en la red interna; este router es la única puerta de entrada.

Endpoints expuestos:
  GET /api/v1/logs/query_range          → consulta de logs con LogQL (rango)
  GET /api/v1/logs/query                → consulta puntual (instantánea)
  GET /api/v1/logs/labels               → lista de labels disponibles
  GET /api/v1/logs/series               → series (streams) que matchean un selector
  GET /api/v1/logs/label/{name}/values  → valores de un label (ej. service, instance)

La WebApp construye la query LogQL; el gateway solo reenvía los query params.
"""

import base64
import binascii
import json
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from app.config import settings

logger = logging.getLogger("api-gateway.logs")

router = APIRouter(prefix="/api/v1/logs", tags=["logs"])

_HOP_BY_HOP = {
    "host", "content-length", "content-encoding", "transfer-encoding",
    "connection", "keep-alive", "te", "trailers", "upgrade", "authorization",
}

# Endpoints de la API HTTP de Loki que permitimos (todos de solo lectura).
_ALLOWED = {
    "query_range": "/loki/api/v1/query_range",
    "query":       "/loki/api/v1/query",
    "labels":      "/loki/api/v1/labels",
    "series":      "/loki/api/v1/series",
}


def _roles_from_bearer(request: Request) -> list[str]:
    """
    Extrae `realm_access.roles` del JWT que el middleware global YA validó.

    No re-verifica la firma: cuando JWT_ENABLED=true, el middleware valida el
    token antes de que la request llegue a este router. Solo decodificamos el
    payload para leer los roles.
    """
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return []
    token = auth.split(" ", 1)[1].strip()
    parts = token.split(".")
    if len(parts) != 3:
        return []
    try:
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)  # padding base64url
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
    except (ValueError, binascii.Error, json.JSONDecodeError):
        return []
    return payload.get("realm_access", {}).get("roles", [])


def _require_superadmin(request: Request) -> None:
    # En modo dev (JWT deshabilitado) no se aplica gate, coherente con el resto
    # del sistema. En producción, JWT_ENABLED=true y se exige el rol.
    if not settings.JWT_ENABLED:
        return

    # 1) El middleware JWT ya validó el token e inyectó la identidad como header
    #    (y ELIMINÓ el Authorization). Ese es el camino normal en este gateway.
    if request.headers.get("x-user-role", "") == "superAdmin":
        return

    # 2) Fallback: si en algún flujo el Authorization sigue presente, lo usamos.
    if "superAdmin" in _roles_from_bearer(request):
        return

    raise HTTPException(
        status_code=403,
        detail="Solo el superadministrador puede consultar los logs",
    )


async def _forward_get(request: Request, target_url: str) -> Response:
    if request.query_params:
        target_url += f"?{request.query_params}"

    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }

    logger.info("LOGS %s → %s", request.url.path, target_url)
    client: httpx.AsyncClient = request.app.state.http_client

    try:
        upstream = await client.request("GET", target_url, headers=headers)
    except httpx.ConnectError:
        logger.error("Sin conexión a Loki: %s", settings.LOKI_URL)
        raise HTTPException(status_code=503, detail="Loki no disponible")
    except httpx.TimeoutException:
        logger.error("Timeout esperando a Loki")
        raise HTTPException(status_code=504, detail="Timeout de Loki")

    resp_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )


@router.get("/label/{name}/values", summary="Valores de un label (service, instance, …)")
async def label_values(name: str, request: Request):
    _require_superadmin(request)
    target = f"{settings.LOKI_URL}/loki/api/v1/label/{name}/values"
    return await _forward_get(request, target)


@router.get("/{endpoint}", summary="Proxy de solo lectura a la API de Loki")
async def loki_proxy(endpoint: str, request: Request):
    if endpoint not in _ALLOWED:
        raise HTTPException(status_code=404, detail="Endpoint de logs no soportado")
    _require_superadmin(request)
    target = f"{settings.LOKI_URL}{_ALLOWED[endpoint]}"
    return await _forward_get(request, target)
