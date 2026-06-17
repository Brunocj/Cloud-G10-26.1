"""
VM Placement — PUCP Cloud Orchestrator
Microservicio HTTP que implementa CP-SAT (Google OR-Tools) para asignación
óptima de VMs a workers físicos con overcommit estadístico por dimensión.

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
from app.nats_responder import nats_responder

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("vm-placement")


# ── App ───────────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("VM Placement service starting up.")
    # Conectar al bus NATS para escuchar slice.placement.process (BYOS)
    try:
        await nats_responder.connect()
    except Exception as exc:
        logger.warning("[NATS] No se pudo conectar al bus NATS: %s (solo HTTP disponible)", exc)
    yield
    await nats_responder.disconnect()
    logger.info("VM Placement service shutting down.")


app = FastAPI(
    title="VM Placement",
    description=(
        "Asignación óptima de VMs a workers usando CP-SAT (Google OR-Tools). "
        "Implementa un knapsack determinístico multidimensional con capacidades "
        "efectivas ajustadas estadísticamente (chance-constraint approximation)."
    ),
    version="3.0.0",
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
    Recibe las VMs a desplegar (vcpus, ram_gb, disco_gb) y el Servers' State
    (capacidad efectiva disponible por worker y dimensión, ya con OC_r[j] aplicado
    por el Slice Manager), y devuelve el mapa vm_id → worker_id usando CP-SAT.

    Timeout dinámico: n segundos, donde n = número de VMs del slice.
    El límite se pasa directamente al solver vía max_time_in_seconds.
    Si el solver no encuentra solución factible en ese tiempo, retorna FAILED.
    """
    n = len(request.vms)
    timeout_seconds = max(float(n), 1.0)  # mínimo 1 segundo

    logger.info(
        f"[{request.slice_id}] Placement request — az_id={request.availability_zone_id} "
        f"vms={n} workers={len(request.workers)} timeout={timeout_seconds}s"
    )

    try:
        success, placement_map, reason, detail = await asyncio.get_event_loop().run_in_executor(
            None,
            run_placement,
            request.vms,
            request.workers,
            timeout_seconds,
            request.availability_zone_id,   # ← Strategy selector (BYOS)
        )
    except Exception as exc:
        logger.error(f"[{request.slice_id}] Unexpected error in placement engine: {exc}", exc_info=True)
        return PlacementResponse(
            slice_id=request.slice_id,
            status="FAILED",
            placement_map=None,
            reason="INTERNAL_ERROR",
            detail="Error inesperado en el motor de placement.",
        )

    if success:
        logger.info(
            f"[{request.slice_id}] Placement SUCCESS — {len(placement_map)} VMs asignadas."
        )
        return PlacementResponse(
            slice_id=request.slice_id,
            status="SUCCESS",
            placement_map=placement_map,
            reason=None,
            detail=None,
        )
    else:
        logger.warning(
            f"[{request.slice_id}] Placement FAILED — reason={reason} detail={detail}"
        )
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
