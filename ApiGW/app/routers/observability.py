"""
app/routers/observability.py
Proxy transparente: /api/v1/observability/** → Observability service (puerto 8006)

Autónomo: no depende de app.proxy ni app.config. Solo httpx y os.
"""

import os
import logging
import httpx
from fastapi import APIRouter, Request
from fastapi.responses import Response

logger = logging.getLogger("api-gateway.observability")

router = APIRouter(prefix="/api/v1/observability", tags=["Observability"])

# URL del servicio de Observabilidad. Ajustar en docker-compose.yml.
# Si Observability corre en OTRA red que no sea pucp_cloud_net, usar:
#   OBSERVABILITY_URL=http://host.docker.internal:8006
#   OBSERVABILITY_URL=http://10.20.11.212:8006
OBSERVABILITY_URL = os.getenv("OBSERVABILITY_URL", "http://observability:8006").rstrip("/")
FORWARD_TIMEOUT   = float(os.getenv("FORWARD_TIMEOUT", "30.0"))


@router.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
)
async def proxy_observability(path: str, request: Request) -> Response:
    target = f"{OBSERVABILITY_URL}/{path}"
    if request.url.query:
        target = f"{target}?{request.url.query}"

    logger.info("%s /api/v1/observability/%s → %s", request.method, path, target)

    hop_by_hop = {"host", "connection", "keep-alive", "proxy-authenticate",
                  "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}
    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() not in hop_by_hop}

    body = await request.body()

    try:
        async with httpx.AsyncClient(timeout=FORWARD_TIMEOUT) as client:
            upstream = await client.request(
                method=request.method,
                url=target,
                headers=fwd_headers,
                content=body,
            )
    except httpx.ConnectError as e:
        logger.error("No se pudo conectar a Observability en %s: %s", target, e)
        return Response(
            content=f'{{"detail":"Observability service unreachable at {OBSERVABILITY_URL}"}}',
            status_code=502,
            media_type="application/json",
        )
    except httpx.TimeoutException:
        logger.error("Timeout al conectar a %s", target)
        return Response(
            content='{"detail":"Observability service timeout"}',
            status_code=504,
            media_type="application/json",
        )
    except Exception as e:
        logger.exception("Error inesperado en proxy_observability: %s", e)
        return Response(
            content='{"detail":"Proxy error"}',
            status_code=500,
            media_type="application/json",
        )

    resp_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in hop_by_hop}

    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )
