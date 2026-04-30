import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.config import settings
from app.routers import slices

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("api-gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Cliente HTTP compartido para todos los routers (connection pool)
    app.state.http_client = httpx.AsyncClient(timeout=settings.FORWARD_TIMEOUT)
    logger.info("API Gateway iniciado. Slice Manager: %s", settings.SLICE_MANAGER_URL)
    yield
    await app.state.http_client.aclose()
    logger.info("API Gateway detenido.")


app = FastAPI(
    title="PUCP Cloud Orchestrator — API Gateway",
    version="1.0.0",
    lifespan=lifespan,
)

# ──────────────────────────────────────────────────────────────────
# Routers
# ──────────────────────────────────────────────────────────────────
app.include_router(slices.router)


# ──────────────────────────────────────────────────────────────────
# Health del propio gateway
# ──────────────────────────────────────────────────────────────────
@app.get("/health", tags=["gateway"])
async def health():
    return {"status": "ok", "service": "api-gateway"}
