from fastapi import FastAPI
from contextlib import asynccontextmanager
import asyncio

from app.database import engine, Base
from app.routers import slice_router, deploy_router
from app.routers import image_router
from app.nats_producer import nats_producer
from app.services.placement_worker import process_placement_worker
from app.services.nats_listener import nats_result_listener
from app.services.gc_scheduler import gc_scheduler_task

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

    yield
    
    # 3. Al detener el servidor, limpiamos procesos
    placement_task.cancel()
    result_task.cancel()
    gc_task.cancel()
    await nats_producer.disconnect()

app = FastAPI(title="Slice Manager Orchestrator", lifespan=lifespan)

# Montamos nuestras rutas
app.include_router(slice_router.router)
app.include_router(deploy_router.router)
app.include_router(image_router.router)
app.include_router(image_router.router)

@app.get("/")
def health_check():
    return {"status": "✅ Slice Manager Online (Modo Híbrido)"}