import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import ENABLE_DASHBOARD
from app.targets import load_targets
from app.services.scheduler import scheduler_loop
from routers.metrics_router import router as metrics_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
logger = logging.getLogger("observability")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting observability microservice")
    load_targets()
    task = asyncio.create_task(scheduler_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    logger.info("Observability microservice stopped")


app = FastAPI(
    title="Observability — PUCP Cloud",
    version="1.0.0",
    lifespan=lifespan,
)

app.include_router(metrics_router)

if ENABLE_DASHBOARD:
    from routers.dashboard_router import router as dashboard_router
    app.include_router(dashboard_router)
    logger.info("Dashboard enabled at /dashboard")
else:
    logger.info("Dashboard disabled (ENABLE_DASHBOARD=false)")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def root():
    links = {"health": "/health", "metrics": "/metrics/workers", "docs": "/docs"}
    if ENABLE_DASHBOARD:
        links["dashboard"] = "/dashboard"
    return {"service": "observability", "links": links}
