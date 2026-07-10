"""
Lógica de proxy compartida entre todos los routers.
"""

import logging

import httpx
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger("api-gateway.proxy")

# Headers que no se propagan entre proxies (RFC 7230 §6.1)
_HOP_BY_HOP = {
    "connection", "keep-alive", "transfer-encoding",
    "te", "trailers", "upgrade", "host", "content-length",
}


def build_forward_headers(request: Request, extra: dict | None = None) -> dict:
    """
    Filtra los headers hop-by-hop del request entrante.

    El parámetro `extra` permite inyectar headers adicionales hacia el upstream.
    Se usa cuando JWT está activo para pasar la identidad extraída del token:

        extra = {
            "X-User-Id":   payload["sub"],
            "X-User-Role": payload["realm_access"]["roles"][0],
        }
    """
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }
    if extra:
        headers.update(extra)
    return headers


async def forward(
    request: Request,
    target_url: str,
    extra_headers: dict | None = None,
) -> Response:
    """
    Reenvía el request al upstream y devuelve la respuesta al cliente.
    Conserva método, headers, query string y body exactamente como llegan.
    """
    if request.query_params:
        target_url += f"?{request.query_params}"

    headers = build_forward_headers(request, extra_headers)
    body    = await request.body()

    logger.info("%s %s → %s", request.method, request.url.path, target_url)

    client: httpx.AsyncClient = request.app.state.http_client

    try:
        upstream = await client.request(
            method=request.method,
            url=target_url,
            headers=headers,
            content=body,
        )
    except httpx.ConnectError:
        logger.error("Sin conexión al upstream: %s", target_url)
        return Response(
            '{"detail":"Servicio no disponible"}',
            status_code=503,
            media_type="application/json",
        )
    except httpx.TimeoutException:
        logger.error("Timeout esperando upstream: %s", target_url)
        return Response(
            '{"detail":"Timeout del servicio upstream"}',
            status_code=504,
            media_type="application/json",
        )

    response_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _HOP_BY_HOP
    }

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=response_headers,
        media_type=upstream.headers.get("content-type"),
    )
