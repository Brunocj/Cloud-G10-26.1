import asyncio
import httpx
import logging
import hashlib
import time
from fastapi import FastAPI, BackgroundTasks, HTTPException, Depends
from sqlalchemy.orm import Session
from contextlib import asynccontextmanager

from app.utils import extract_vms_for_placement, validate_topology_graph
from app.telemetry import get_real_worker_metrics

import uuid # Para generar el request_id
from app.nats_producer import nats_producer # Importamos nuestro cliente

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
    db_generator = get_db()
    db = next(db_generator)

    while True:
        request_data = await placement_queue.get()
        slice_id = request_data["slice_id"]
        zone = request_data["zone"]
        
        try:
            logger.info(f"[{slice_id}] Iniciando proceso de Placement para la zona: {zone}...")

            # 1. Recuperamos el Slice de la Base de Datos
            db_slice = db.query(Slice).filter(Slice.id == slice_id).first()
            
            if not db_slice or not db_slice.topology_json:
                logger.error(f"[{slice_id}] Slice no encontrado o no tiene topología guardada.")
                placement_queue.task_done()
                continue

            # 2. VALIDACIÓN FU-01: Verificamos el grafo antes de hacer cualquier cálculo
            if not validate_topology_graph(db_slice.topology_json):
                logger.error(f"[{slice_id}] Topología inválida. Fallo en validación de grafo.")
                db_slice.state = SliceState.FAILED
                db.commit()
                placement_queue.task_done()
                continue
            
            # 3. EXTRACCIÓN DINÁMICA DE RECURSOS
            dynamic_vms = extract_vms_for_placement(db_slice.topology_json)
            
            if not dynamic_vms:
                logger.error(f"[{slice_id}] No se encontraron VMs en la topología para desplegar.")
                db_slice.state = SliceState.FAILED
                db.commit()
                placement_queue.task_done()
                continue

            # 4. ARMAMOS EL PAYLOAD 
            # Inyectamos las VMs extraídas del lienzo. Los workers siguen simulados por ahora.
            payload = {
                "slice_id": str(slice_id),
                "availability_zone": zone,
                "vms": dynamic_vms, 
                "workers": [
                    { "worker_id": "server-1", "available_vcpus": 10, "available_ram_mb": 16000, "available_disk_gb": 500 },
                    { "worker_id": "server-2", "available_vcpus": 10, "available_ram_mb": 16000, "available_disk_gb": 500 },
                    { "worker_id": "server-3", "available_vcpus": 10, "available_ram_mb": 16000, "available_disk_gb": 500 },
                    { "worker_id": "server-4", "available_vcpus": 10, "available_ram_mb": 16000, "available_disk_gb": 500 }
                ]
            }
            

            # --- CONEXIÓN REAL AL MÓDULO DE VM PLACEMENT ---
            logger.info(f"[{slice_id}] Solicitando Placement a: {VM_PLACEMENT_URL}")
            
            try:
                # Realizamos el POST HTTP síncrono/asíncrono hacia el contenedor de VM Placement. El timeout es importante para no dejar colgado el worker.
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(VM_PLACEMENT_URL, json=payload)
                    response.raise_for_status() # Lanza error si responde 4xx o 5xx
                    
                    # Extraemos el JSON de respuesta que estructuró el módulo de VM Placement
                    placement_result = response.json()
                    
            except httpx.RequestError as exc:
                logger.error(f"[{slice_id}] Falló la red hacia VM Placement: {exc}")
                placement_result = {"status": "FAILED"}
            except httpx.HTTPStatusError as exc:
                logger.error(f"[{slice_id}] VM Placement devolvió error HTTP {exc.response.status_code}")
                placement_result = {"status": "FAILED"}

            # --- VALIDACIÓN DEL RESULTADO ---
            if placement_result.get("status") == "SUCCESS":
                placement_map = placement_result.get("placement_map", [])
                logger.info(f"[{slice_id}] Placement REAL exitoso: {placement_map}")
                
                # --- NUEVA LÓGICA: DICCIONARIO DE INFRAESTRUCTURA ---
                server_inventory = {
                    "server-1": {"ip": "10.0.10.2", "user": "ubuntu", "key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"},
                    "server-2": {"ip": "10.0.10.3", "user": "ubuntu", "key": "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----"}
                }

                vms_dict = {vm["vm_id"]: vm for vm in dynamic_vms}
                vm_tap_counters = {vm["vm_id"]: 0 for vm in dynamic_vms}
                vlan_counter = 100
                global_mac_counter = 0

                # Identificador único del slice para generar MACs determinísticas
                slice_hash = hashlib.sha256(str(slice_id).encode()).hexdigest()
                mac_prefix = f"52:54:00:{slice_hash[:2]}:{slice_hash[2:4]}"

                network_links = []

                # 2. Iteramos sobre los cables dibujados por el alumno
                for edge in edges:
                    vm1_id = edge["source"]
                    vm2_id = edge["target"]
                    
                    if vm1_id not in vms_dict or vm2_id not in vms_dict:
                        continue

                    # Recuperamos a qué servidor físico fue asignada cada VM
                    worker1_id = next(w["worker_id"] for w in placement_map if w["vm_id"] == vm1_id)
                    worker2_id = next(w["worker_id"] for w in placement_map if w["vm_id"] == vm2_id)
                    
                    # Recuperamos credenciales de los servidores
                    worker1 = server_inventory.get(worker1_id, {})
                    worker2 = server_inventory.get(worker2_id, {})

                    # Generamos los nombres de los puertos virtuales (TAPs)
                    tap1 = f"tap-{vm1_id}-{vm_tap_counters[vm1_id]}"
                    tap2 = f"tap-{vm2_id}-{vm_tap_counters[vm2_id]}"
                    
                    # Generamos las MACs
                    mac1 = f"{mac_prefix}:{global_mac_counter:02x}".upper()
                    global_mac_counter += 1
                    mac2 = f"{mac_prefix}:{global_mac_counter:02x}".upper()
                    global_mac_counter += 1

                    # Inyectamos el TAP en la memoria de la VM (Para el Compute Provisioner)
                    vms_dict[vm1_id].setdefault("tap_interfaces", []).append({"tap_name": tap1, "mac": mac1})
                    vms_dict[vm2_id].setdefault("tap_interfaces", []).append({"tap_name": tap2, "mac": mac2})

                    # Armamos el enlace lógico (Para el Network Orchestrator)
                    network_links.append({
                        "connection_id": f"{vm1_id}-{vm2_id}-{vlan_counter}",
                        "vlan_id": vlan_counter,
                        "vm1_id": vm1_id,
                        "vm1_worker_ip": worker1.get("ip"),
                        "vm1_tap": tap1,
                        "vm1_ssh_user": worker1.get("user"),
                        "vm1_ssh_private_key": worker1.get("key"),
                        "vm1_security_rules": [], 
                        "vm2_id": vm2_id,
                        "vm2_worker_ip": worker2.get("ip"),
                        "vm2_tap": tap2,
                        "vm2_ssh_user": worker2.get("user"),
                        "vm2_ssh_private_key": worker2.get("key"),
                        "vm2_security_rules": []
                    })
                    
                    vm_tap_counters[vm1_id] += 1
                    vm_tap_counters[vm2_id] += 1
                    vlan_counter += 1

                # 3. Construimos la lista final de VMs enriquecidas con sus TAPs
                vms_payload = []
                for vm_id, original_vm in vms_dict.items():
                    worker_id = next(w["worker_id"] for w in placement_map if w["vm_id"] == vm_id)
                    server_info = server_inventory.get(worker_id, {})
                    
                    vms_payload.append({
                        "vm_id": vm_id,
                        "worker_ip": server_info.get("ip"),
                        "ssh_user": server_info.get("user"),
                        "ssh_private_key": server_info.get("key"),
                        "vcpus": original_vm.get("vcpus"),
                        "ram_mb": original_vm.get("ram_mb"),
                        "image_name": "ubuntu-22.04.qcow2",
                        "tap_interfaces": original_vm.get("tap_interfaces", []), # <--- AQUI INYECTAMOS LOS TAPS
                        "priority": 0
                    })

                # 4. Armamos el contrato final DeploySliceRequest
                queue_manager_payload = {
                    "slice_id": str(slice_id),
                    "request_id": f"req-{uuid.uuid4().hex[:8]}",
                    "vms": vms_payload,
                    "links": network_links  # <--- AQUI INYECTAMOS LOS CABLES
                }
                
                # Publicamos en NATS (¡El inicio de la Saga!)
                published = await nats_producer.publish_deploy(queue_manager_payload)
                
                if published:
                    db_slice.state = SliceState.PROVISIONING
                else:
                    db_slice.state = SliceState.FAILED
                    logger.error(f"[{slice_id}] Falló el envío a NATS. Abortando despliegue.")
                    
                db.commit()
            else:
                db_slice.state = SliceState.FAILED
                db.commit()
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
    # Encendemos NATS y el Worker
    await nats_producer.connect() # Conecta a localhost:4222 por defecto
    worker_task = asyncio.create_task(process_placement_worker())
    yield
    # Apagamos ordenadamente
    worker_task.cancel()
    await nats_producer.disconnect()

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