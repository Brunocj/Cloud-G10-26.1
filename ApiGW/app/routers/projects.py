"""
app/routers/projects.py — Proxy transparente /api/v1/projects/** → Slice Manager
Mismo patrón que slices.py.
"""
import logging

import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from app.config import settings

logger = logging.getLogger("api-gateway.projects")

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])

_FORWARDED_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}
_HOP_BY_HOP_HEADERS = {
    "host", "content-length", "transfer-encoding", "connection",
    "keep-alive", "te", "trailers", "upgrade",
}


def _build_forward_headers(request: Request) -> dict:
    return {
        k: v for k, v in request.headers.items()
        if k.lower() not in _HOP_BY_HOP_HEADERS
    }


@router.api_route(
    "/{path:path}",
    methods=list(_FORWARDED_METHODS),
    summary="Reenvía requests de projects al Slice Manager",
)
async def forward_projects_request(path: str, request: Request):
    target_url = f"{settings.SLICE_MANAGER_URL}/api/v1/projects/{path}"
    if request.query_params:
        target_url += f"?{request.query_params}"

    headers = _build_forward_headers(request)
    body = await request.body()

    logger.info("%s %s → %s", request.method, request.url.path, target_url)

    client: httpx.AsyncClient = request.app.state.http_client
    try:
        upstream = await client.request(
            method=request.method, url=target_url,
            headers=headers, content=body,
        )
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="Slice Manager no disponible")
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="Timeout del Slice Manager")

    resp_headers = {
        k: v for k, v in upstream.headers.items()
        if k.lower() not in _HOP_BY_HOP_HEADERS
    }
    return Response(
        content=upstream.content,
        status_code=upstream.status_code,
        headers=resp_headers,
        media_type=upstream.headers.get("content-type"),
    )
