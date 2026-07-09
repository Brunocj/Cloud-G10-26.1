import logging
from fastapi import FastAPI
from contextlib import asynccontextmanager
import asyncio

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

from app.database import engine
# OJO: los modelos usan su PROPIO declarative_base (app/models.py), no el de
# app/database.py — hay que usar ese Base para que create_all vea las tablas.
from app.models import Base
from app.routers import slice_router, deploy_router
from app.routers import image_router
from app.routers import project_router, user_router
from app.routers import approval_router, notification_router
from app.routers import audit_router, infra_router
from app.nats_producer import nats_producer
from app.services.placement_worker import process_placement_worker
from app.services.nats_listener import nats_result_listener
from app.services.gc_scheduler import gc_scheduler_task
from app.services.ttl_scheduler import ttl_scheduler_task

# Creamos las tablas si no existen
Base.metadata.create_all(bind=engine)

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Conectamos al bus NATS
    await nats_producer.connect()
    
    # 2. Levantamos los workers asíncronos en background
    placement_task = asyncio.create_task(process_placement_worker())
    result_task = asyncio.create_task(nats_result_listener())
    gc_task = asyncio.create_task(gc_scheduler_task())
    ttl_task = asyncio.create_task(ttl_scheduler_task())

    yield

    # 3. Al detener el servidor, limpiamos procesos
    placement_task.cancel()
    result_task.cancel()
    gc_task.cancel()
    ttl_task.cancel()
    await nats_producer.disconnect()

app = FastAPI(title="Slice Manager Orchestrator", lifespan=lifespan)

# Montamos nuestras rutas
app.include_router(approval_router.router)      # antes que slice_router (prefijo /requests)
app.include_router(slice_router.router)
app.include_router(deploy_router.router)
app.include_router(image_router.router)
app.include_router(project_router.router)
app.include_router(user_router.router)
app.include_router(notification_router.router)
app.include_router(audit_router.router)
app.include_router(infra_router.router)

@app.get("/")
def health_check():
    return {"status": "✅ Slice Manager Online (Modo Híbrido)"}