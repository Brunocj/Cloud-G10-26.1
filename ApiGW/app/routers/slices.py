import logging

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from starlette.requests import ClientDisconnect

from app.config import settings

logger = logging.getLogger("api-gateway.slices")

router = APIRouter(prefix="/api/v1/slices", tags=["slices"])

# Rutas (dentro del prefijo) que requieren timeout extendido por operaciones lentas
# sobre infraestructura remota (Glance/OpenStack via tunnel SSH SOCKS5)
_LONG_TIMEOUT_PATHS = {
    "utils/images/upload",   # Subida de imagen a NFS o Glance (puede tardar minutos)
}

# Metodos HTTP que se reenvian al Slice Manager
_FORWARDED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}

# Headers que NO se reenvian al Slice Manager
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
    Construye los headers que se enviaran al Slice Manager.

    Pasa todos los headers del cliente excepto los hop-by-hop.
    El JWT middleware ya inyecto X-User-Id y X-User-Role en el scope,
    y elimino Authorization, por lo que aqui solo se filtra hop-by-hop.
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

    NOTA: Se usa request.body() (NO request.stream()) porque el JWT middleware
    usa BaseHTTPMiddleware que invalida el stream ASGI tras mutar los headers.
    Usar request.stream() en ese contexto lanza ClientDisconnect.
    request.body() es seguro porque Starlette lo cachea internamente.
    """
    target_url = f"{settings.SLICE_MANAGER_URL}/api/v1/slices/{path}"
    if request.query_params:
        target_url += f"?{request.query_params}"

    headers = _build_forward_headers(request)

    # Leer body completo — seguro con BaseHTTPMiddleware porque Starlette cachea
    # el resultado de request.body() en request._body tras la primera lectura.
    try:
        body = await request.body()
    except ClientDisconnect:
        logger.warning("Cliente desconectado antes de leer el body: %s %s", request.method, path)
        raise HTTPException(status_code=499, detail="El cliente cerró la conexión")

    logger.info("%s %s -> %s (body=%d bytes)", request.method, request.url.path, target_url, len(body))

    client: httpx.AsyncClient = request.app.state.http_client

    # Determinar si esta ruta necesita un timeout extendido
    use_long_timeout = path.rstrip("/") in _LONG_TIMEOUT_PATHS
    if use_long_timeout:
        effective_timeout = httpx.Timeout(
            connect=10.0,
            read=settings.IMAGE_UPLOAD_TIMEOUT,
            write=settings.IMAGE_UPLOAD_TIMEOUT,
            pool=5.0,
        )
        logger.info("[timeout-override] %s -> %.0fs (IMAGE_UPLOAD_TIMEOUT)", path, settings.IMAGE_UPLOAD_TIMEOUT)
    else:
        effective_timeout = None  # usa el default del cliente (FORWARD_TIMEOUT)

    try:
        upstream_response = await client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body,
            **({"timeout": effective_timeout} if effective_timeout else {}),
        )
    except httpx.ConnectError:
        logger.error("No se pudo conectar al Slice Manager en %s", settings.SLICE_MANAGER_URL)
        raise HTTPException(status_code=503, detail="Slice Manager no disponible")
    except httpx.TimeoutException:
        logger.error("Timeout esperando respuesta del Slice Manager para: %s", path)
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
