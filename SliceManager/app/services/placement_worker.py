import asyncio
import httpx
import logging
import json
import os
import hashlib
import uuid
import random # <-- No olvides importar random arriba del archivo
from app.database import SessionLocal
from app.models import Slice, Vm, Vlan, Image
from app.telemetry import get_real_worker_metrics
from app.nats_producer import nats_producer

logger = logging.getLogger("SliceManager.Worker")
VM_PLACEMENT_URL = os.getenv("VM_PLACEMENT_URL", "http://vm-placement:8080/placement")

# Cola global
placement_queue = asyncio.Queue()

async def process_placement_worker():
    """Worker asíncrono que procesa los despliegues uno por uno"""
    db = SessionLocal() # Instancia dedicada para el worker en background

    while True:
        request_data = await placement_queue.get()
        slice_id = request_data["slice_id"]
        zone = request_data["zone"]
        
        try:
            logger.info(f"[{slice_id}] Iniciando proceso de Placement...")
            db_slice = db.query(Slice).filter(Slice.id == slice_id).first()
            
            if not db_slice:
                placement_queue.task_done()
                continue

            # 1. EXTRACCIÓN DESDE LA BASE DE DATOS RELACIONAL
            vms_de_bd = db.query(Vm).filter(Vm.slice_id == slice_id).all()
            
            if not vms_de_bd:
                db_slice.status = "FAILED"
                db.commit()
                placement_queue.task_done()
                continue

            # Formateamos VMs para el VM Placement (Módulo de Go/Python externo)
            # Formateamos VMs para el VM Placement 
            dynamic_vms = []
            for vm in vms_de_bd:
                dynamic_vms.append({
                    "vm_id": vm.name,
                    "vcpus": int(vm.vcore), # 🔥 Aseguramos int
                    "ram_mb": float(vm.ram), # 🔥 Aseguramos float (adiós Decimal)
                    "disk_gb": float(vm.disk)  # 🔥 Aseguramos float
                })

            # 2. TELEMETRÍA Y VM PLACEMENT
            real_workers_metrics = await get_real_worker_metrics()
            
            # 🔥 FIX JSON DECIMAL: Limpiamos los datos de los workers antes de enviarlos
            for worker in real_workers_metrics:
                # Convertimos Decimal a float/int
                worker["available_vcpus"] = int(worker.get("available_vcpus", 0))
                worker["available_ram_mb"] = float(worker.get("available_ram_mb", 0))
                worker["available_disk_gb"] = float(worker.get("available_disk_gb", 0))

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

            # 3. ENRIQUECIMIENTO DEL CONTRATO SI EL PLACEMENT FUE EXITOSO
            if placement_result.get("status") == "SUCCESS":
                placement_map = placement_result.get("placement_map", [])
                
                def get_ssh_key(filepath: str) -> str:
                    if not os.path.exists(filepath):
                        return ""
                    with open(filepath, "r") as key_file:
                        return key_file.read()

                server_inventory = {
                    1: {"ip": "10.0.10.1", "user": "ubuntu", "key_path": "keys/worker1.pem"},
                    2: {"ip": "10.0.10.2", "user": "ubuntu", "key_path": "keys/worker2.pem"},
                    3: {"ip": "10.0.10.3", "user": "ubuntu", "key_path": "keys/worker3.pem"},
                    4: {"ip": "10.0.10.4", "user": "ubuntu", "key_path": "keys/worker4.pem"}
                }

                # Obtenemos los edges guardados en el JSON
                slice_json = db_slice.slice_json
                if isinstance(slice_json, str):
                    slice_json = json.loads(slice_json)
                edges = slice_json.get("edges", []) if slice_json else []

                vms_dict = {vm.name: vm for vm in vms_de_bd}
                # 🔥 PASO 1: Obtenemos una lista de todas las VLANs que están en uso actualmente en la BD
                vlans_ocupadas_db = db.query(Vlan.id).all()
                vlans_ocupadas = [v[0] for v in vlans_ocupadas_db] # Lo aplanamos a una lista simple
                
                def obtener_vlan_libre():
                    """Busca una VLAN aleatoria entre 100 y 4000 que no esté en uso"""
                    while True:
                        vid = random.randint(100, 4000)
                        if vid not in vlans_ocupadas:
                            vlans_ocupadas.append(vid) # La marcamos como ocupada en memoria para el siguiente enlace
                            return vid
                # 🔥 FIX: Generamos un prefijo MAC único por cada Slice
                slice_hash = hashlib.sha256(str(slice_id).encode()).hexdigest()
                mac_prefix = f"52:54:00:{slice_hash[:2]}:{slice_hash[2:4]}"
                global_mac_counter = 0
                network_links = []
                vms_payload_data = {vm.name: {"tap_interfaces": []} for vm in vms_de_bd}

                # Generamos los enlaces
                for edge in edges:
                    vm1_id = edge.get("from", edge.get("source"))
                    vm2_id = edge.get("to", edge.get("target"))
                    
                    if not vm1_id or not vm2_id or vm1_id not in vms_dict or vm2_id not in vms_dict:
                        continue

                    worker1_id = next(w["worker_id"] for w in placement_map if w["vm_id"] == vm1_id)
                    worker2_id = next(w["worker_id"] for w in placement_map if w["vm_id"] == vm2_id)
                    
                    worker1 = server_inventory.get(worker1_id, {})
                    worker2 = server_inventory.get(worker2_id, {})

                    tap1 = f"t-{str(slice_id)[-3:]}-{vm1_id[:4]}-{vm2_id[:4]}"
                    tap2 = f"t-{str(slice_id)[-3:]}-{vm2_id[:4]}-{vm1_id[:4]}"
                    
                    mac1 = f"{mac_prefix}:{global_mac_counter:02x}".upper()
                    global_mac_counter += 1
                    mac2 = f"{mac_prefix}:{global_mac_counter:02x}".upper()
                    global_mac_counter += 1

                    # 🔥 PASO 2: Obtenemos el ID libre de VLAN
                    vlan_actual = obtener_vlan_libre()

                    vms_payload_data[vm1_id]["tap_interfaces"].append({"tap_name": tap1, "mac": mac1})
                    vms_payload_data[vm2_id]["tap_interfaces"].append({"tap_name": tap2, "mac": mac2})

                    network_links.append({
                        "connection_id": f"{vm1_id}-{vm2_id}-{vlan_actual}",
                        "vlan_id": vlan_actual,  # 🔥 Usamos la VLAN aleatoria
                        "vm1_id": vm1_id,
                        "vm1_worker_ip": worker1.get("ip", "0.0.0.0"),
                        "vm1_tap": tap1,
                        "vm1_ssh_user": worker1.get("user", "ubuntu"),
                        "vm1_ssh_private_key": get_ssh_key(worker1.get("key_path", "")),
                        "vm2_id": vm2_id,
                        "vm2_worker_ip": worker2.get("ip", "0.0.0.0"),
                        "vm2_tap": tap2,
                        "vm2_ssh_user": worker2.get("user", "ubuntu"),
                        "vm2_ssh_private_key": get_ssh_key(worker2.get("key_path", ""))
                    })
                    
                    # 🔥 PASO 3: Guardamos la VLAN explícitamente en la BD con su ID real
                    db.add(Vlan(id=vlan_actual, slice_id=slice_id, type="p2p"))
                vnc_por_worker = {}
                for w_id in server_inventory.keys():
                    vnc_ocupados_bd = db.query(Vm.vnc_port).filter(
                        Vm.worker_id == w_id,
                        Vm.vnc_port.isnot(None)
                    ).all()
                    vnc_por_worker[w_id] = set(v[0] for v in vnc_ocupados_bd)   

                # 🔥 LÓGICA R5: Matemáticas para la subred interna (10.0.0.0/8)
                octeto_2 = (int(slice_id) // 256) % 256
                octeto_3 = int(slice_id) % 256
                ip_host_counter = 10  # Empezamos a dar IPs desde la .10

                # Construimos la lista final de VMs
                vms_payload = []
                for vm in vms_de_bd:
                    # worker_id ahora es un número entero puro (ej: 3)
                    worker_id = next(w["worker_id"] for w in placement_map if w["vm_id"] == vm.name)
                    server_info = server_inventory.get(worker_id, {})
                    
                    vm.worker_id = worker_id
                    
                    # ESTRATEGIA VNC: Pool aleatorio protegido
                    if not vm.vnc_port:
                        while True:
                            candidato = random.randint(5901, 5999) # Rango estándar VNC
                            if candidato not in vnc_por_worker[worker_id]:
                                vm.vnc_port = candidato
                                # Lo añadimos al set global para protegerlo de la sig. VM
                                vnc_por_worker[worker_id].add(candidato) 
                                break
                    
                    # 🔥 CONSULTA DINÁMICA DE LA IMAGEN
                    image_obj = db.query(Image).filter(Image.id == vm.image_id).first()
                    
                    if image_obj and image_obj.path:
                        img_path = image_obj.path # ✅ Mandamos la ruta exacta y absoluta
                    else:
                        img_path = "" # Error

                    # 🔥 LÓGICA R5: Asignamos la IP interna a esta VM
                    ip_interna_asignada = f"10.{octeto_2}.{octeto_3}.{ip_host_counter}"
                    ip_host_counter += 1  # Aumentamos para la siguiente VM (11, 12, 13...)
                    
                    vms_payload.append({
                        "vm_id": vm.name,
                        "worker_ip": server_info.get("ip", "0.0.0.0"),
                        "ssh_user": server_info.get("user", "ubuntu"),
                        "ssh_private_key": get_ssh_key(server_info.get("key_path", "")),
                        "vcpus": int(vm.vcore),
                        "ram_mb": float(vm.ram),
                        "disk_gb": float(vm.disk),
                        "image_path": img_path,
                        "vnc_port": vm.vnc_port, 
                        "vnc_display": vm.vnc_port - 5900,
                        "tap_interfaces": vms_payload_data[vm.name]["tap_interfaces"],
                        
                        # --- NUEVOS CAMPOS DEL REQUERIMIENTO R5 ---
                        "internet_access": getattr(vm, 'internet_access', 0), # Usamos getattr por si SQLite/MySQL aún no sincronizó la columna
                        "external_ip": vm.external_ip,
                        "internal_ip": ip_interna_asignada # 🔥 ¡La inyectamos al contrato NATS!
                        # ------------------------------------------
                    })

                # 4. PUBLICACIÓN EN NATS
                queue_manager_payload = {
                    "slice_id": str(slice_id), 
                    "request_id": f"req-{uuid.uuid4().hex[:8]}",
                    "vms": vms_payload,
                    "links": network_links
                }

                # 🔥 FIX DE RAÍZ: Guardamos la receta exacta en la BD
                slice_json = db_slice.slice_json
                if isinstance(slice_json, str):
                    slice_json = json.loads(slice_json)
                if not slice_json:
                    slice_json = {}
                    
                slice_json["deployed_vms"] = vms_payload
                slice_json["deployed_links"] = network_links
                db_slice.slice_json = slice_json

                published = await nats_producer.publish_deploy(queue_manager_payload)
                db_slice.status = "PROVISIONING" if published else "FAILED"
                db.commit()
            else:
                db_slice.status = "FAILED"
                db.commit()
                
        except Exception as e:
            logger.error(f"[{slice_id}] Error procesando placement: {str(e)}")
            # 🔥 FIX: Si el worker crashea, actualizamos el estado a FAILED para no dejar Zombies
            if 'db_slice' in locals() and db_slice:
                db_slice.status = "FAILED"
                db.commit()
        finally:
            placement_queue.task_done()