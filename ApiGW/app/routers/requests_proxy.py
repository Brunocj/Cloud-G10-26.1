# app/routers/requests_proxy.py
"""
Proxies transparentes hacia el Slice Manager para los prefijos nuevos:
  /api/v1/requests/**     → flujo de aprobaciones
  /api/v1/audit/**        → bitácora de eventos
  /api/v1/infra/**        → gestión de infraestructura (superAdmin)
  /api/v1/maintenance/**  → limpieza de BD de slices/VMs (admin/superAdmin)
"""
import logging

from fastapi import APIRouter, Request

from app.config import settings
from app.proxy import forward

logger = logging.getLogger("api-gateway.requests")

router = APIRouter(tags=["slice-manager-proxies"])

_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


@router.api_route("/api/v1/requests/{path:path}", methods=_METHODS,
                  summary="Reenvía el flujo de aprobaciones al Slice Manager")
async def forward_request(path: str, request: Request):
    return await forward(request, f"{settings.SLICE_MANAGER_URL}/api/v1/requests/{path}")


@router.api_route("/api/v1/audit/{path:path}", methods=_METHODS,
                  summary="Reenvía la bitácora al Slice Manager")
async def forward_audit(path: str, request: Request):
    return await forward(request, f"{settings.SLICE_MANAGER_URL}/api/v1/audit/{path}")


@router.api_route("/api/v1/infra/{path:path}", methods=_METHODS,
                  summary="Reenvía la gestión de infraestructura al Slice Manager")
async def forward_infra(path: str, request: Request):
    return await forward(request, f"{settings.SLICE_MANAGER_URL}/api/v1/infra/{path}")


@router.api_route("/api/v1/maintenance/{path:path}", methods=_METHODS,
                  summary="Reenvía el mantenimiento de BD al Slice Manager")
async def forward_maintenance(path: str, request: Request):
    return await forward(request, f"{settings.SLICE_MANAGER_URL}/api/v1/maintenance/{path}")
