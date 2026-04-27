import asyncio
import httpx
import logging
from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager

# Importamos lo que creamos en los pasos anteriores
from app.database import engine, Base, get_db
from app.models import Slice, SliceState, User
from app.schemas import DeployRequest, DraftSaveRequest

# Crea las tablas en la base de datos si no existen
Base.metadata.create_all(bind=engine)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("SliceManager")

# URL del microservicio de VM Placement
VM_PLACEMENT_URL = "http://vm-placement:8080/placement"

# Cola interna para procesar el placement de forma secuencial
placement_queue = asyncio.Queue()

async def process_placement_worker():
    """Worker en segundo plano que procesa la cola de despliegues secuencialmente"""
    while True:
        request_data = await placement_queue.get()
        slice_id = request_data["slice_id"]
        zone = request_data["zone"]
        
        try:
            logger.info(f"[{slice_id}] Iniciando proceso de Placement para la zona: {zone}...")

            # --- SIMULACIÓN DEL MÓDULO DE VM PLACEMENT ---
            logger.info(f"[{slice_id}] Esperando 2 segundos simulando red...")
            await asyncio.sleep(2) # Simulamos que la petición tarda en ir y volver

            # Simulamos que el VMPlacement nos responde un JSON exitoso
            simulated_response = {
                "status": "SUCCESS",
                "placement_map": [{"vm_id": "vm-1", "worker_id": "server-1"}]
            }

            if simulated_response["status"] == "SUCCESS":
                logger.info(f"[{slice_id}] Placement SIMULADO exitoso: {simulated_response['placement_map']}")
                # En el futuro aquí actualizarás la BD y se enviará al módulo de Colas para el Compute Provisioner
            else:
                logger.error(f"[{slice_id}] Fallo en Placement simulado.")
            
            # Aquí simulamos el payload que le enviaremos VMPlacement.
            # En el flujo final, leerás el JSON de la topología desde la BD.
            '''
            payload = {
                "slice_id": str(slice_id),
                "availability_zone": zone,
                "vms": [{"vm_id": "vm-1", "vcpus": 2, "ram_mb": 2048, "disk_gb": 20}],
                "workers": [
                    {"worker_id": "server-1", "available_vcpus": 16, "available_ram_mb": 32000, "available_disk_gb": 500}
                ]
            }
            '''
            '''
            async with httpx.AsyncClient() as client:
                response = await client.post(VM_PLACEMENT_URL, json=payload, timeout=10.0)
                
                if response.status_code == 200:
                    placement_result = response.json()
                    if placement_result.get("status") == "SUCCESS":
                        logger.info(f"[{slice_id}] Placement exitoso: {placement_result.get('placement_map')}")
                        # TODO: Actualizar BD y enviar al módulo de Colas para el Compute Provisioner
                    else:
                        logger.error(f"[{slice_id}] Fallo en Placement: {placement_result.get('reason')}")
                else:
                    logger.error(f"[{slice_id}] Error de comunicación con VM Placement.")
            '''     
        except Exception as e:
            logger.error(f"[{slice_id}] Error procesando placement: {str(e)}")
        finally:
            placement_queue.task_done()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Se ejecuta al iniciar la aplicación
    worker_task = asyncio.create_task(process_placement_worker())
    yield
    # Se ejecuta al apagar la aplicación
    worker_task.cancel()

app = FastAPI(title="Slice Manager Orchestrator", lifespan=lifespan)

@app.post("/slices/draft", status_code=201)
def save_draft(request: DraftSaveRequest, db: Session = Depends(get_db)):
    """REQ-US-07: Guarda el borrador del lienzo en la base de datos"""
    # Para la prueba, simulamos un dueño con ID 1
    new_slice = Slice(
        name=request.name,
        owner_id=1, 
        state=SliceState.DRAFT,
        topology_json=request.topology_json,
        availability_zone="pending"
    )
    db.add(new_slice)
    db.commit()
    db.refresh(new_slice)
    return {"message": "Borrador guardado", "slice_id": new_slice.id}

@app.post("/slices/{slice_id}/deploy", status_code=202)
async def request_deploy(slice_id: int, request: DeployRequest, db: Session = Depends(get_db)):
    """REQ-US-08: Solicita el despliegue de un slice guardado"""
    db_slice = db.query(Slice).filter(Slice.id == slice_id).first()
    
    if not db_slice:
        raise HTTPException(status_code=404, detail="Slice no encontrado")
        
    # Actualizamos el estado y los datos elegidos en el Checkout
    db_slice.state = SliceState.PENDING_APPROVAL
    db_slice.ttl_hours = request.ttl_hours
    db_slice.availability_zone = request.availability_zone
    db.commit()

    # Encolamos la solicitud para que el worker la procese de forma segura
    await placement_queue.put({
        "slice_id": slice_id,
        "zone": request.availability_zone
    })
    
    return {"status": "ACCEPTED", "message": "Solicitud encolada para validación de recursos."}