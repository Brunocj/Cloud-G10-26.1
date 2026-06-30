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
VM_PLACEMENT_URL  = os.getenv("VM_PLACEMENT_URL",  "http://vm-placement:8080/placement")
OBSERVABILITY_URL = os.getenv("OBSERVABILITY_URL", "http://observability:8006")

# Umbrales de uso en vivo: workers que superen estos valores son excluidos
# como candidatos al placement (gate previo al solver).
MAX_CPU_USAGE_PCT = float(os.getenv("MAX_CPU_USAGE_PCT", "95"))
MAX_RAM_USAGE_PCT = float(os.getenv("MAX_RAM_USAGE_PCT", "90"))

# Factores de overcommit de arranque por dimensión (usados si Observabilidad
# aún no ha calculado OC_r[j] para el worker)
OC_CPU_DEFAULT   = 2.0
OC_RAM_DEFAULT   = 1.54   # 1/0.65
OC_DISCO_DEFAULT = 1.0    # sin overcommit


async def fetch_worker_usage() -> dict:
    """
    Consulta Observability `/metrics/workers` y devuelve un dict
    { worker_id (int): { 'cpu_pct': float, 'ram_pct': float } }.

    Fallback: si Observability no responde dentro del timeout, retorna {} y
    el filtro de elegibilidad queda inactivo (no se bloquea el despliegue).
    """
    url = f"{OBSERVABILITY_URL.rstrip('/')}/metrics/workers"
    try:
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
            r = await client.get(url)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.warning("[USAGE_GATE] Observability no respondió (%s) — sin filtro de uso", e)
        return {}

    workers = data.get("workers", data) if isinstance(data, dict) else data
    usage = {}
    if isinstance(workers, list):
        for w in workers:
            try:
                wid = int(w.get("worker_id") or w.get("id"))
                usage[wid] = {
                    "cpu_pct": float(w.get("live_cpu_usage_pct") or 0),
                    "ram_pct": float(w.get("live_ram_usage_pct") or 0),
                }
            except (TypeError, ValueError):
                continue
    return usage

# Cola global
placement_queue = asyncio.Queue()

async def process_placement_worker():
    """Worker asíncrono que procesa los despliegues uno por uno"""

    while True:
        request_data = await placement_queue.get()

        slice_id = request_data["slice_id"]
        zone_id  = request_data["zone_id"]
        db = SessionLocal()

        try:
            logger.info(f"[{slice_id}] Iniciando proceso de Placement...")
            db_slice = db.query(Slice).filter(Slice.id == slice_id).first()

            if not db_slice:
                continue

            # ── 1. EXTRACCIÓN DE VMs DESDE BD ─────────────────────────────────
            vms_de_bd = db.query(Vm).filter(Vm.slice_id == slice_id).all()

            if not vms_de_bd:
                db_slice.status = "FAILED"
                db.commit()
                continue

            # ── 2. CONSTRUCCIÓN DEL SERVERS' STATE MULTIDIMENSIONAL ───────────
            # Para cada worker de la zona:
            #   C_efectivo_r[j] = C_nominal_r[j] × OC_r[j]
            #   disponible_r[j] = C_efectivo_r[j] − Σ recurso_r(VMs ACTIVE en j)
            #
            # OC_r[j] se lee desde la BD (calculado por Observabilidad).
            # Si aún no existe, se usa el valor referencial de arranque.

            workers_zona = db.query(Worker).filter(
                Worker.availability_zones_id == zone_id
            ).all()

            if not workers_zona:
                logger.error("[PLACEMENT] ❌ No hay workers en la zona id=%s", zone_id)
                db_slice.status = "FAILED"
                db.commit()
                placement_queue.task_done()
                continue

            # Consumo agregado de VMs ACTIVE por worker y por dimensión
            consumo_cpu = dict(
                db.query(Vm.worker_id, func.sum(Vm.vcore))
                .filter(Vm.state == "ACTIVE", Vm.worker_id.isnot(None))
                .group_by(Vm.worker_id).all()
            )
            consumo_ram = dict(
                db.query(Vm.worker_id, func.sum(Vm.ram))
                .filter(Vm.state == "ACTIVE", Vm.worker_id.isnot(None))
                .group_by(Vm.worker_id).all()
            )
            consumo_disco = dict(
                db.query(Vm.worker_id, func.sum(Vm.disk))
                .filter(Vm.state == "ACTIVE", Vm.worker_id.isnot(None))
                .group_by(Vm.worker_id).all()
            )

            # Uso en vivo de cada worker (CPU%/RAM%) reportado por Observability.
            # Workers que superen los umbrales se excluyen del placement.
            worker_usage = await fetch_worker_usage()

            servers_state = []
            excluded_by_usage = []
            for w in workers_zona:
                # Gate de elegibilidad por uso real reportado
                u = worker_usage.get(w.id)
                if u is not None:
                    if u["ram_pct"] > MAX_RAM_USAGE_PCT or u["cpu_pct"] > MAX_CPU_USAGE_PCT:
                        excluded_by_usage.append((w.id, u["cpu_pct"], u["ram_pct"]))
                        logger.warning(
                            "[USAGE_GATE] Worker-%d excluido: cpu=%.1f%% (max %.0f%%) "
                            "ram=%.1f%% (max %.0f%%)",
                            w.id, u["cpu_pct"], MAX_CPU_USAGE_PCT,
                            u["ram_pct"], MAX_RAM_USAGE_PCT
                        )
                        continue

                # Capacidades nominales del worker
                cpu_nominal   = float(w.cpu or 0)
                ram_nominal   = float(w.ram or 0) / 1024.0   # MB → GB
                disco_nominal = float(w.disk_gb or 0)

                # Factores OC_r[j] desde BD (columnas calculadas por Observabilidad)
                # Si la columna no existe aún (bootstrap), se usa el valor por defecto
                oc_cpu   = float(getattr(w, 'oc_cpu',   None) or OC_CPU_DEFAULT)
                oc_ram   = float(getattr(w, 'oc_ram',   None) or OC_RAM_DEFAULT)
                oc_disco = float(getattr(w, 'oc_disco', None) or OC_DISCO_DEFAULT)

                # Capacidades efectivas
                c_ef_cpu   = cpu_nominal   * oc_cpu
                c_ef_ram   = ram_nominal   * oc_ram
                c_ef_disco = disco_nominal * oc_disco

                # Consumo ya comprometido por VMs activas
                usado_cpu   = float(consumo_cpu.get(w.id,   0) or 0)
                usado_ram   = float(consumo_ram.get(w.id,   0) or 0) / 1024.0  # MB → GB
                usado_disco = float(consumo_disco.get(w.id, 0) or 0)

                disp_cpu   = round(c_ef_cpu   - usado_cpu,   4)
                disp_ram   = round(c_ef_ram   - usado_ram,   4)
                disp_disco = round(c_ef_disco - usado_disco, 4)

                servers_state.append({
                    "worker_id":        w.id,
                    "disponible_cpu":   disp_cpu,
                    "disponible_ram":   disp_ram,
                    "disponible_disco": disp_disco,
                })
                logger.info(
                    "[SERVERS_STATE] Worker-%d | "
                    "cpu: nom=%.1f oc=%.2f ef=%.1f usado=%.1f disp=%.1f | "
                    "ram(GB): nom=%.1f oc=%.2f ef=%.1f usado=%.1f disp=%.1f | "
                    "disco(GB): nom=%.1f oc=%.2f ef=%.1f usado=%.1f disp=%.1f",
                    w.id,
                    cpu_nominal,   oc_cpu,   c_ef_cpu,   usado_cpu,   disp_cpu,
                    ram_nominal,   oc_ram,   c_ef_ram,   usado_ram,   disp_ram,
                    disco_nominal, oc_disco, c_ef_disco, usado_disco, disp_disco,
                )

            if not servers_state:
                logger.error(
                    "[PLACEMENT] ❌ Sin workers elegibles en zona_id=%s "
                    "(excluidos por uso: %d)", zone_id, len(excluded_by_usage)
                )
                db_slice.status = "FAILED"
                db.commit()
                placement_queue.task_done()
                continue

            # Formateamos VMs con recursos crudos para el VM Placement
            dynamic_vms = []
            for vm in vms_de_bd:
                dynamic_vms.append({
                    "vm_id":    vm.name,
                    "vcpus":    float(vm.vcore  or 1),
                    "ram_gb":   float(vm.ram    or 512) / 1024.0,  # MB → GB
                    "disco_gb": float(vm.disk   or 5),
                })

            payload = {
                "slice_id":          str(slice_id),
                "availability_zone": str(zone_id),
                "vms":     dynamic_vms,
                "workers": servers_state,
            }

            logger.info("[PLACEMENT] 👉 VM Placement invocado para %d VM(s) en zona_id=%s",
                        len(dynamic_vms), zone_id)
            for dv in dynamic_vms:
                logger.info("[PLACEMENT]    VM: %-20s vcpus=%.1f ram_gb=%.2f disco_gb=%.1f",
                            dv['vm_id'], dv['vcpus'], dv['ram_gb'], dv['disco_gb'])

            async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
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
                    logger.info("[PLACEMENT]    %s → Worker-%s",
                                asig.get('vm_id'), asig.get('worker_id'))

            # ── 3. ENRIQUECIMIENTO DEL CONTRATO SI EL PLACEMENT FUE EXITOSO ──
            if placement_result.get("status") == "SUCCESS":
                placement_map = placement_result.get("placement_map", [])

                def get_ssh_key(filepath: str) -> str:
                    if not filepath:
                        logger.error("[PLACEMENT] ❌ ssh_key_path es None/vacío en BD para este worker.")
                        return ""
                    filename = os.path.basename(filepath)
                    container_path = f"/app/keys/{filename}"
                    candidates = [filepath, container_path]
                    for path in candidates:
                        exists = os.path.exists(path)
                        logger.info("[PLACEMENT] 🔑 Buscando clave en '%s' → existe=%s", path, exists)
                        if exists:
                            try:
                                content = open(path, "r").read()
                                logger.info("[PLACEMENT] ✅ Clave leída de '%s' len=%d", path, len(content))
                                return content
                            except Exception as exc:
                                logger.error("[PLACEMENT] ❌ Error leyendo '%s': %s", path, exc)
                    # Listar /app/keys para diagnóstico
                    try:
                        keys_dir = "/app/keys"
                        files = os.listdir(keys_dir) if os.path.isdir(keys_dir) else []
                        logger.error("[PLACEMENT] ❌ Clave no encontrada. Archivos en %s: %s", keys_dir, files)
                    except Exception as exc:
                        logger.error("[PLACEMENT] ❌ No se pudo listar /app/keys: %s", exc)
                    return ""

                # Construir el inventario leyendo credenciales SSH desde la BD
                server_inventory = {}
                for w in workers_zona:
                    server_inventory[w.id] = {
                        "ip": w.ip,
                        "port": getattr(w, "ssh_port", None),
                        "user": getattr(w, "ssh_user", None),
                        "key_path": getattr(w, "ssh_key_path", None)
                    }

                slice_json = db_slice.slice_json
                if isinstance(slice_json, str):
                    slice_json = json.loads(slice_json)
                edges = slice_json.get("edges", []) if slice_json else []

                vms_dict          = {vm.name: vm for vm in vms_de_bd}
                vlans_ocupadas_db = db.query(Vlan.id).all()
                vlans_ocupadas    = [v[0] for v in vlans_ocupadas_db]

                def obtener_vlan_libre():
                    while True:
                        vid = random.randint(100, 4000)
                        if vid not in vlans_ocupadas:
                            vlans_ocupadas.append(vid)
                            return vid

                slice_hash         = hashlib.sha256(str(slice_id).encode()).hexdigest()
                mac_prefix         = f"52:54:00:{slice_hash[:2]}:{slice_hash[2:4]}"
                global_mac_counter = 0
                network_links      = []
                vms_payload_data   = {vm.name: {"tap_interfaces": []} for vm in vms_de_bd}

                # TAP de gestión para cada VM
                for vm in vms_de_bd:
                    tap_mgmt = f"t-{str(slice_id)[-3:]}-{vm.name[:4]}-m"
                    mac_mgmt = f"{mac_prefix}:{global_mac_counter:02x}".upper()
                    global_mac_counter += 1
                    vms_payload_data[vm.name]["tap_interfaces"].append(
                        {"tap_name": tap_mgmt, "mac": mac_mgmt}
                    )

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
                        "vm1_worker_port":     worker1.get("port", 22),
                        "vm1_tap":             tap1,
                        "vm1_ssh_user":        worker1.get("user", "ubuntu"),
                        "vm1_ssh_private_key": get_ssh_key(worker1.get("key_path", "")),
                        "vm2_id":              vm2_id,
                        "vm2_worker_ip":       worker2.get("ip", "0.0.0.0"),
                        "vm2_worker_port":     worker2.get("port", 22),
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

                vnc_por_worker = {}
                for w_id in server_inventory.keys():
                    vnc_ocupados_bd = db.query(Vm.vnc_port).filter(
                        Vm.worker_id == w_id,
                        Vm.vnc_port.isnot(None)
                    ).all()
                    vnc_por_worker[w_id] = set(v[0] for v in vnc_ocupados_bd)

                octeto_2        = (int(slice_id) // 256) % 256
                octeto_3        = int(slice_id) % 256
                ip_host_counter = 10

                vms_payload = []
                for vm in vms_de_bd:
                    logger.info("[PLACEMENT] 🔧 Procesando VM: %s", vm.name)
                    worker_id   = next(w["worker_id"] for w in placement_map if w["vm_id"] == vm.name)
                    server_info = server_inventory.get(worker_id, {})
                    vm.worker_id = worker_id

                    if not vm.vnc_port:
                        while True:
                            candidato = random.randint(5901, 5999)
                            if candidato not in vnc_por_worker.get(worker_id, set()):
                                vm.vnc_port = candidato
                                vnc_por_worker.setdefault(worker_id, set()).add(candidato)
                                logger.info("[PLACEMENT]    VNC asignado: VM %-20s → Worker-%d puerto %d",
                                            vm.name, worker_id, candidato)
                                break

                    image_obj = db.query(Image).filter(Image.id == vm.image_id).first()
                    img_path  = image_obj.path if image_obj and image_obj.path else ""

                    nodo_ui = next((n for n in slice_json.get("nodes", []) if n.get("id") == vm.name), {})
                    if not img_path:
                        img_path = nodo_ui.get("image", "")

                    ip_interna_asignada = f"10.{octeto_2}.{octeto_3}.{ip_host_counter}"
                    ip_host_counter += 1

                    image_name = image_obj.name if image_obj else nodo_ui.get("image", "ubuntu")
                    image_name_lower = image_name.lower().split("-")[0].split(".")[0]
                    vm_user     = nodo_ui.get("vm_user")     or image_name_lower
                    vm_password = nodo_ui.get("vm_password") or "pucp2026"

                    vms_payload.append({
                        "vm_id":           vm.name,
                        "vm_label":        nodo_ui.get("label") or vm.name,
                        "slice_name":      db_slice.name or str(slice_id),
                        "worker_ip":       server_info.get("ip", "0.0.0.0"),
                        "worker_port":     server_info.get("port", 22),
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

                # ── 4. PUBLICACIÓN EN NATS ─────────────────────────────────────
                logger.info("="*70)
                logger.info("[PLACEMENT] 📤 Publicando en NATS → QueueManager")
                logger.info("[PLACEMENT]    slice_id=%s  VMs=%d  links=%d",
                            slice_id, len(vms_payload), len(network_links))

                queue_manager_payload = {
                    "slice_id":             str(slice_id),
                    "request_id":           f"req-{uuid.uuid4().hex[:8]}",
                    "availability_zone_id": zone_id,
                    "vms":                  vms_payload,
                    "links":                network_links,
                    "workers":              servers_state
                }

                slice_json = db_slice.slice_json
                if isinstance(slice_json, str):
                    slice_json = json.loads(slice_json)
                if not slice_json:
                    slice_json = {}

                slice_json["deployed_vms"]   = vms_payload
                slice_json["deployed_links"] = network_links
                db_slice.slice_json           = dict(slice_json)
                db_slice.availability_zone_id = zone_id

                logger.info("[PLACEMENT %s] Guardando deployed_vms (%d), deployed_links (%d), az_id=%s",
                            slice_id, len(vms_payload), len(network_links), zone_id)
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
            db.rollback()
            if 'db_slice' in locals() and db_slice:
                try:
                    db_slice.status = "FAILED"
                    db.commit()
                except Exception as rollback_err:
                    logger.error(f"[{slice_id}] No se pudo actualizar el estado del slice a FAILED: {rollback_err}")
        finally:
            db.close()
            placement_queue.task_done()
