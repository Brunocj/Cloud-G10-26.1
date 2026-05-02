import asyncio
import httpx
import logging
import json
import os
import hashlib
import uuid
from app.database import get_db
from app.models import Topology, Vm
from app.telemetry import get_real_worker_metrics
from app.nats_producer import nats_producer

logger = logging.getLogger("SliceManager.Worker")
VM_PLACEMENT_URL = os.getenv("VM_PLACEMENT_URL", "http://vm-placement:8080/placement")

# Cola global
placement_queue = asyncio.Queue()

async def process_placement_worker():
    """Worker en background que procesa los despliegues"""
    db_generator = get_db()
    db = next(db_generator)

    while True:
        request_data = await placement_queue.get()
        slice_id = request_data["slice_id"]
        zone = request_data["zone"]
        
        try:
            logger.info(f"[{slice_id}] Iniciando proceso de Placement...")
            db_slice = db.query(Topology).filter(Topology.id == slice_id).first()
            
            if not db_slice:
                placement_queue.task_done()
                continue

            # --- 3. EXTRACCIÓN DESDE LA BASE DE DATOS RELACIONAL ---
            vms_de_bd = db.query(Vm).filter(Vm.topologies_id == slice_id).all()
            
            if not vms_de_bd:
                db_slice.status = "FAILED"
                db.commit()
                placement_queue.task_done()
                continue

            # Convertimos las VMs de la BD al formato que espera el VM Placement
            dynamic_vms = []
            for vm in vms_de_bd:
                dynamic_vms.append({
                    "vm_id": vm.name,
                    "vcpus": int(vm.vcore),
                    "ram_mb": int(vm.ram),
                    "external_ip": getattr(vm, 'external_ip', None) # Extraemos si existe
                })

            # 4. Telemetría y 5. Petición a VM Placement (El código se mantiene igual)
            real_workers_metrics = await get_real_worker_metrics()
            payload = {
                "slice_id": str(slice_id),
                "availability_zone": zone,
                "vms": dynamic_vms, 
                "workers": real_workers_metrics
            }

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(VM_PLACEMENT_URL, json=payload)
                response.raise_for_status()
                placement_result = response.json()

            if placement_result.get("status") == "SUCCESS":
                placement_map = placement_result.get("placement_map", [])
                
                # (Aquí va tu lógica de server_inventory, lectura de llaves SSH, MACs y TAPs)
                # ... (Pega la lógica de generación de contratos que ya tenías) ...
                
                # Publicamos en NATS
                queue_manager_payload = {
                    "slice_id": str(slice_id),
                    "request_id": f"req-{uuid.uuid4().hex[:8]}",
                    # "vms": vms_payload, 
                    # "links": network_links
                }
                
                published = await nats_producer.publish_deploy(queue_manager_payload)
                db_slice.status = "PROVISIONING" if published else "FAILED"
                db.commit()
            else:
                db_slice.status = "FAILED"
                db.commit()
                
        except Exception as e:
            logger.error(f"[{slice_id}] Error procesando placement: {str(e)}")
        finally:
            placement_queue.task_done()