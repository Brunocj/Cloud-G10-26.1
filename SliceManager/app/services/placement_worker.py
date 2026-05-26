import asyncio
import httpx
import logging
import json
import os
import hashlib
import uuid
import random
from app.database import SessionLocal
from app.models import Slice, Vm, Vlan, Image, Worker
from sqlalchemy import func
from app.nats_producer import nats_producer

logger = logging.getLogger("SliceManager.Worker")
VM_PLACEMENT_URL = os.getenv("VM_PLACEMENT_URL", "http://vm-placement:8080/placement")

# Cola global
placement_queue = asyncio.Queue()

async def process_placement_worker():
    """Worker asíncrono que procesa los despliegues uno por uno"""
    db = SessionLocal()

    while True:
        request_data = await placement_queue.get()

        slice_id = request_data["slice_id"]
        zone_id  = request_data["zone_id"]

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

            # 2. CONSTRUCCIÓN DEL SERVERS' STATE DESDE BD (con overprovisioning)
            F_OP = 1.0 / 0.65  # Factor de overprovisioning inicial (~1.54)

            workers_zona = db.query(Worker).filter(
                Worker.availability_zones_id == zone_id,
                Worker.id != 1
            ).all()

            if not workers_zona:
                logger.error("[PLACEMENT] ❌ No hay workers en la zona id=%s", zone_id)
                db_slice.status = "FAILED"
                db.commit()
                placement_queue.task_done()
                continue

            pesos_activos = dict(
                db.query(Vm.worker_id, func.sum(Vm.peso_actualizado))
                .filter(Vm.state == "ACTIVE", Vm.worker_id.isnot(None))
                .group_by(Vm.worker_id)
                .all()
            )

            servers_state = []
            for w in workers_zona:
                ram_gb           = float(w.ram) / 1024.0 if w.ram else 0.0
                disk_gb          = float(w.disk_gb) if w.disk_gb else 0.0
                capacidad_nominal = 3 * int(w.cpu or 0) + 5 * ram_gb + 1 * disk_gb
                C_i              = capacidad_nominal * F_OP
                sum_pesos        = float(pesos_activos.get(w.id, 0.0) or 0.0)
                D_i              = round(C_i - sum_pesos, 4)
                servers_state.append({"worker_id": w.id, "available_weight": D_i})
                logger.info(
                    "[SERVERS_STATE] Worker-%d | cap_nominal=%.2f | C_i=%.2f | pesos_activos=%.2f | D_i=%.2f",
                    w.id, capacidad_nominal, C_i, sum_pesos, D_i
                )

            # Formateamos VMs con peso para el VM Placement
            dynamic_vms = []
            for vm in vms_de_bd:
                dynamic_vms.append({
                    "vm_id": vm.name,
                    "peso": float(vm.peso_actualizado or vm.peso or 0.0)
                })

            payload = {
                "slice_id": str(slice_id),
                "availability_zone": str(zone_id),
                "vms": dynamic_vms,
                "workers": [
                    {"worker_id": w["worker_id"], "disponible": w["available_weight"]}
                    for w in servers_state
                ]
            }

            logger.info("[PLACEMENT] 👉 VM Placement invocado para %d VM(s) en zona_id=%s", len(dynamic_vms), zone_id)
            for dv in dynamic_vms:
                logger.info("[PLACEMENT]    VM: %-20s peso: %.4f", dv['vm_id'], dv['peso'])

            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.post(VM_PLACEMENT_URL, json=payload)
                if response.status_code == 422:
                    logger.error("[PLACEMENT] 422 detalle: %s", response.text)
                response.raise_for_status()
                placement_result = response.json()

            placement_status = placement_result.get('status')
            placement_map    = placement_result.get('placement_map', [])
            logger.info("[PLACEMENT] ✅ VM Placement respondió: status=%s  asignaciones=%d",
                        placement_status, len(placement_map))
            if placement_status == 'SUCCESS':
                for asig in placement_map:
                    logger.info("[PLACEMENT]    %s → Worker-%s", asig.get('vm_id'), asig.get('worker_id'))

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

                slice_json = db_slice.slice_json
                if isinstance(slice_json, str):
                    slice_json = json.loads(slice_json)
                edges = slice_json.get("edges", []) if slice_json else []

                vms_dict       = {vm.name: vm for vm in vms_de_bd}
                vlans_ocupadas_db = db.query(Vlan.id).all()
                vlans_ocupadas = [v[0] for v in vlans_ocupadas_db]

                def obtener_vlan_libre():
                    while True:
                        vid = random.randint(100, 4000)
                        if vid not in vlans_ocupadas:
                            vlans_ocupadas.append(vid)
                            return vid

                slice_hash       = hashlib.sha256(str(slice_id).encode()).hexdigest()
                mac_prefix       = f"52:54:00:{slice_hash[:2]}:{slice_hash[2:4]}"
                global_mac_counter = 0
                network_links    = []
                vms_payload_data = {vm.name: {"tap_interfaces": []} for vm in vms_de_bd}

                # TAP de gestión para cada VM
                for vm in vms_de_bd:
                    tap_mgmt = f"t-{str(slice_id)[-3:]}-{vm.name[:4]}-m"
                    mac_mgmt = f"{mac_prefix}:{global_mac_counter:02x}".upper()
                    global_mac_counter += 1
                    vms_payload_data[vm.name]["tap_interfaces"].append({"tap_name": tap_mgmt, "mac": mac_mgmt})

                # Generamos los enlaces
                for edge in edges:
                    vm1_id = edge.get("from", edge.get("source"))
                    vm2_id = edge.get("to",   edge.get("target"))

                    if not vm1_id or not vm2_id or vm1_id not in vms_dict or vm2_id not in vms_dict:
                        continue

                    worker1_id = next(w["worker_id"] for w in placement_map if w["vm_id"] == vm1_id)
                    worker2_id = next(w["worker_id"] for w in placement_map if w["vm_id"] == vm2_id)
                    worker1    = server_inventory.get(worker1_id, {})
                    worker2    = server_inventory.get(worker2_id, {})

                    tap1 = f"t-{str(slice_id)[-3:]}-{vm1_id[:4]}-{vm2_id[:4]}"
                    tap2 = f"t-{str(slice_id)[-3:]}-{vm2_id[:4]}-{vm1_id[:4]}"

                    mac1 = f"{mac_prefix}:{global_mac_counter:02x}".upper(); global_mac_counter += 1
                    mac2 = f"{mac_prefix}:{global_mac_counter:02x}".upper(); global_mac_counter += 1

                    vlan_actual = obtener_vlan_libre()

                    vms_payload_data[vm1_id]["tap_interfaces"].append({"tap_name": tap1, "mac": mac1})
                    vms_payload_data[vm2_id]["tap_interfaces"].append({"tap_name": tap2, "mac": mac2})

                    network_links.append({
                        "connection_id":       f"{vm1_id}-{vm2_id}-{vlan_actual}",
                        "vlan_id":             vlan_actual,
                        "vm1_id":              vm1_id,
                        "vm1_worker_ip":       worker1.get("ip", "0.0.0.0"),
                        "vm1_tap":             tap1,
                        "vm1_ssh_user":        worker1.get("user", "ubuntu"),
                        "vm1_ssh_private_key": get_ssh_key(worker1.get("key_path", "")),
                        "vm2_id":              vm2_id,
                        "vm2_worker_ip":       worker2.get("ip", "0.0.0.0"),
                        "vm2_tap":             tap2,
                        "vm2_ssh_user":        worker2.get("user", "ubuntu"),
                        "vm2_ssh_private_key": get_ssh_key(worker2.get("key_path", ""))
                    })

                    db.add(Vlan(id=vlan_actual, slice_id=slice_id, type="p2p"))
                    logger.info(
                        "[PLACEMENT] 🔗 Enlace %-20s → %-20s | VLAN: %-4d | "
                        "TAP1: %-28s MAC1: %s | TAP2: %-28s MAC2: %s",
                        vm1_id, vm2_id, vlan_actual, tap1, mac1, tap2, mac2
                    )

                logger.info("[PLACEMENT] ✅ %d enlace(s) procesados: MACs y VLANs asignados", len(edges))
                logger.info("[PLACEMENT] 🔧 Construyendo vnc_por_worker...")  # AÑADIR
                vnc_por_worker = {}

                for w_id in server_inventory.keys():
                    vnc_ocupados_bd = db.query(Vm.vnc_port).filter(
                        Vm.worker_id == w_id,
                        Vm.vnc_port.isnot(None)
                    ).all()
                    vnc_por_worker[w_id] = set(v[0] for v in vnc_ocupados_bd)
                logger.info("[PLACEMENT] 🔧 vnc_por_worker construido: %s", list(vnc_por_worker.keys()))  # AÑADIR
                
                octeto_2     = (int(slice_id) // 256) % 256
                octeto_3     = int(slice_id) % 256
                ip_host_counter = 10

                vms_payload = []
                for vm in vms_de_bd:
                    logger.info("[PLACEMENT] 🔧 Procesando VM: %s", vm.name)
                    worker_id   = next(w["worker_id"] for w in placement_map if w["vm_id"] == vm.name)
                    server_info = server_inventory.get(worker_id, {})
                    vm.worker_id = worker_id
                    logger.info("aaaa")
                    if not vm.vnc_port:
                        while True:
                            candidato = random.randint(5901, 5999)
                            if candidato not in vnc_por_worker[worker_id]:
                                vm.vnc_port = candidato
                                vnc_por_worker[worker_id].add(candidato)
                                logger.info("[PLACEMENT]    VNC asignado: VM %-20s → Worker-%d puerto %d",
                                            vm.name, worker_id, candidato)
                                break
                    logger.info("aaaaasdadsadad")
                    image_obj = db.query(Image).filter(Image.id == vm.image_id).first()
                    img_path  = image_obj.path if image_obj and image_obj.path else ""

                    ip_interna_asignada = f"10.{octeto_2}.{octeto_3}.{ip_host_counter}"
                    ip_host_counter += 1

                    nodo_ui          = next((n for n in slice_json.get("nodes", []) if n.get("id") == vm.name), {})
                    image_name_lower = (image_obj.name if image_obj else "ubuntu").lower().split("-")[0].split(".")[0]
                    vm_user     = nodo_ui.get("vm_user")     or image_name_lower
                    vm_password = nodo_ui.get("vm_password") or "pucp2026"

                    vms_payload.append({
                        "vm_id":           vm.name,
                        "worker_ip":       server_info.get("ip", "0.0.0.0"),
                        "ssh_user":        server_info.get("user", "ubuntu"),
                        "ssh_private_key": get_ssh_key(server_info.get("key_path", "")),
                        "vcpus":           int(vm.vcore),
                        "ram_mb":          float(vm.ram),
                        "disk_gb":         float(vm.disk),
                        "image_path":      img_path,
                        "vnc_port":        vm.vnc_port,
                        "vnc_display":     vm.vnc_port - 5900,
                        "tap_interfaces":  vms_payload_data[vm.name]["tap_interfaces"],
                        "internet_access": getattr(vm, 'internet_access', 0),
                        "external_ip":     vm.external_ip,
                        "internal_ip":     ip_interna_asignada,
                        "vm_user":         vm_user,
                        "vm_password":     vm_password,
                    })

                # 4. PUBLICACIÓN EN NATS
                logger.info("="*70)
                logger.info("[PLACEMENT] 📤 Publicando en NATS → QueueManager")
                logger.info("[PLACEMENT]    slice_id=%s  VMs=%d  links=%d",
                            slice_id, len(vms_payload), len(network_links))
                for vp in vms_payload:
                    logger.info("[PLACEMENT]    VM: %-20s worker_ip=%-12s vnc_port=%s  taps=%d",
                                vp['vm_id'], vp['worker_ip'], vp['vnc_port'],
                                len(vp.get('tap_interfaces', [])))

                queue_manager_payload = {
                    "slice_id":   str(slice_id),
                    "request_id": f"req-{uuid.uuid4().hex[:8]}",
                    "vms":        vms_payload,
                    "links":      network_links
                }

                slice_json = db_slice.slice_json
                if isinstance(slice_json, str):
                    slice_json = json.loads(slice_json)
                if not slice_json:
                    slice_json = {}

                slice_json["deployed_vms"]   = vms_payload
                slice_json["deployed_links"] = network_links
                db_slice.slice_json = dict(slice_json)

                logger.info(f"[PLACEMENT {slice_id}] Guardando deployed_vms ({len(vms_payload)}) y deployed_links ({len(network_links)})")

                db.commit()
                logger.info("[PLACEMENT] 💾 deployed_vms/links persistidos en BD")

                published = await nats_producer.publish_deploy(queue_manager_payload)
                if published:
                    db_slice.status = "PROVISIONING"
                    logger.info("[PLACEMENT] 🟠 Estado del slice cambiado a PROVISIONING")
                else:
                    db_slice.status = "FAILED"
                    logger.error("[PLACEMENT] ❌ Fallo al publicar en NATS, estado → FAILED")
                db.commit()
                logger.info("[PLACEMENT] 🏁 Flujo de placement completado para slice=%s", slice_id)
                logger.info("="*70)
            else:
                db_slice.status = "FAILED"
                db.commit()

        except Exception as e:
            logger.error(f"[{slice_id}] Error procesando placement: {str(e)}", exc_info=True)
            if 'db_slice' in locals() and db_slice:
                db_slice.status = "FAILED"
                db.commit()
        finally:
            placement_queue.task_done()