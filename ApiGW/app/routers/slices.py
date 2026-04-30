import logging

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from app.config import settings

logger = logging.getLogger("api-gateway.slices")

router = APIRouter(prefix="/api/v1/slices", tags=["slices"])

# Métodos HTTP que se reenvían al Slice Manager
_FORWARDED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

# Headers que NO se reenvían al Slice Manager
_HOP_BY_HOP_HEADERS = {
    "host",
    "content-length",
    "transfer-encoding",
    "connection",
    "keep-alive",
    "te",
    "trailers",
    "upgrade",
}


def _build_forward_headers(request: Request) -> dict:
    """
    Construye los headers que se enviarán al Slice Manager.

    Ahora:   pasa todos los headers del cliente excepto los hop-by-hop.
    Futuro:  cuando JWT esté activo, este es el lugar donde se añaden
             los headers de identidad extraídos del token, p. ej.:
               headers["X-User-Id"]   = token_payload["sub"]
               headers["X-User-Role"] = token_payload["realm_access"]["roles"][0]
             y se elimina el header Authorization para que el SM no lo vea.
    """
    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in _HOP_BY_HOP_HEADERS
    }
    return headers


@router.api_route(
    "/{path:path}",
    methods=list(_FORWARDED_METHODS),
    summary="Reenvía requests de slices al Slice Manager",
)
async def forward_slice_request(path: str, request: Request):
    """
    Proxy transparente: recibe cualquier request sobre /api/v1/slices/**
    y lo reenvía al Slice Manager conservando método, headers y body.
    """
    target_url = f"{settings.SLICE_MANAGER_URL}/api/v1/slices/{path}"
    if request.query_params:
        target_url += f"?{request.query_params}"

    headers = _build_forward_headers(request)
    body = await request.body()

    logger.info("%s %s → %s", request.method, request.url.path, target_url)

    client: httpx.AsyncClient = request.app.state.http_client

    try:
        upstream_response = await client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body,
        )
    except httpx.ConnectError:
        logger.error("No se pudo conectar al Slice Manager en %s", settings.SLICE_MANAGER_URL)
        raise HTTPException(status_code=503, detail="Slice Manager no disponible")
    except httpx.TimeoutException:
        logger.error("Timeout esperando respuesta del Slice Manager")
        raise HTTPException(status_code=504, detail="Timeout del Slice Manager")

    # Filtrar headers hop-by-hop de la respuesta antes de devolverla al cliente
    response_headers = {
        key: value
        for key, value in upstream_response.headers.items()
        if key.lower() not in _HOP_BY_HOP_HEADERS
    }

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
        media_type=upstream_response.headers.get("content-type"),
    )
