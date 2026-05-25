"""
VM Placement — PUCP Cloud Orchestrator
Microservicio HTTP que implementa LLF-D para asignación de VMs a workers.

El Slice Manager es el único consumidor de este servicio.
Comunicación: HTTP síncrona POST /placement → JSON response.
"""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.models import PlacementRequest, PlacementResponse
from app.placement_engine import run_placement

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("vm-placement")


# ── App ───────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("VM Placement service starting up.")
    yield
    logger.info("VM Placement service shutting down.")


app = FastAPI(
    title="VM Placement",
    description="Asignación óptima de VMs a workers usando LLF-D.",
    version="2.0.0",
    lifespan=lifespan,
)


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok"}


# ── Placement ─────────────────────────────────────────────────────────────────
@app.post("/placement", response_model=PlacementResponse)
async def placement(request: PlacementRequest):
    """
    Recibe las VMs a desplegar (con peso pre-calculado) y el Servers' State
    (workers con capacidad disponible en unidades de peso), y devuelve el
    mapa vm_id → worker_id usando LLF-D.

    Timeout dinámico: n × 1 segundo, donde n = número de VMs del slice.
    Si el proceso no concluye en ese tiempo, retorna FAILED con reason TIMEOUT.
    """
    n = len(request.vms)
    timeout_seconds = max(n * 1.0, 1.0)  # mínimo 1 segundo

    logger.info(
        f"[{request.slice_id}] Placement request — zone='{request.availability_zone}' "
        f"vms={n} workers={len(request.workers)} timeout={timeout_seconds}s"
    )

    # Ejecutar placement con timeout dinámico
    try:
        success, placement_map, reason, detail = await asyncio.wait_for(
            asyncio.get_event_loop().run_in_executor(
                None,
                run_placement,
                request.vms,
                request.workers,
            ),
            timeout=timeout_seconds,
        )
    except asyncio.TimeoutError:
        logger.warning(f"[{request.slice_id}] Placement TIMEOUT after {timeout_seconds}s")
        return PlacementResponse(
            slice_id=request.slice_id,
            status="FAILED",
            placement_map=None,
            reason="TIMEOUT",
            detail=f"El placement no concluyó en el tiempo máximo permitido ({timeout_seconds}s).",
        )

    if success:
        logger.info(f"[{request.slice_id}] Placement SUCCESS — {len(placement_map)} VMs asignadas.")
        return PlacementResponse(
            slice_id=request.slice_id,
            status="SUCCESS",
            placement_map=placement_map,
            reason=None,
            detail=None,
        )
    else:
        logger.warning(f"[{request.slice_id}] Placement FAILED — reason={reason} detail={detail}")
        return PlacementResponse(
            slice_id=request.slice_id,
            status="FAILED",
            placement_map=None,
            reason=reason,
            detail=detail,
        )


# ── Global error handler ──────────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error."},
    )
