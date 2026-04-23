"""
VM Placement - API HTTP con FastAPI.

Endpoints:
  POST /placement  →  calcula y retorna el mapa VM→Worker
  GET  /health     →  liveness check
"""

import logging
from fastapi import FastAPI
from app.models import PlacementRequest, PlacementResponse
from app.placement_engine import run_placement

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="VM Placement Service",
    description="Asigna VMs a workers físicos usando Round Robin.",
    version="1.0.0",
)


@app.get("/health", tags=["ops"])
def health():
    """Liveness check — confirma que el servicio está levantado."""
    return {"status": "ok"}


@app.post("/placement", response_model=PlacementResponse, tags=["placement"])
def placement(request: PlacementRequest) -> PlacementResponse:
    """
    Calcula la asignación VM→Worker usando Round Robin.

    - Recibe la lista de VMs con sus requerimientos y la lista de workers
      con su capacidad disponible (ya filtrados por availability_zone).
    - Retorna el mapa de asignación o el motivo de fallo.
    """
    return run_placement(request)
