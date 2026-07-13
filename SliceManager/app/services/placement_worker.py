import asyncio
import httpx
import logging
import json
import os
import hashlib
import uuid
import random
from app.database import SessionLocal
from app.models import Slice, Vm, Vlan, Image, Worker, Flavor
from sqlalchemy import func
from app.nats_producer import nats_producer

logger = logging.getLogger("SliceManager.Worker")


def _next_vlan_id(db) -> int:
    """
    `vlans.id` NO tiene AUTO_INCREMENT en el esquema real de la BD — el modo
    legado la usa a propósito como el propio número de VLAN para type='p2p'
    (ver comentario en el modelo `Vlan`), así que la columna nunca tuvo
    default y siempre requiere un valor explícito en el INSERT. Para filas
    type='M'/'C'/'S' (que guardan el número real en `vlan_number`, no en
    `id`) alcanza con cualquier entero libre — se calcula MAX(id)+1.
    """
    return (db.query(func.max(Vlan.id)).scalar() or 0) + 1


def backfill_mgmt_vlans() -> None:
    """
    Migración en caliente (idempotente): registra en `vlans` (type='M') la
    VLAN de gestión de los slices Linux Cluster que ya estaban ACTIVE o
    PROVISIONING antes de este fix — su mgmt_vlan=1000+slice_id ya está
    configurado físicamente en los workers pero nunca quedó reservado en la
    BD, así que un enlace nuevo de OTRO slice podía chocar con él (bug de
    aislamiento entre slices que comparten worker/trunk). Se corre una vez
    al arranque del servicio, antes de procesar la cola de deploys.
    """
    db = SessionLocal()
    try:
        slices = db.query(Slice).filter(
            Slice.availability_zone_id == 1,   # Linux Cluster — OpenStack no usa mgmt_vlan
            Slice.status.in_(("ACTIVE", "PROVISIONING")),
        ).all()
        added = 0
        for sl in slices:
            mgmt_vlan = 1000 + int(sl.id)
            exists = db.query(Vlan.id).filter(
                Vlan.slice_id == sl.id, Vlan.type == "M",
                Vlan.vlan_number == mgmt_vlan).first()
            if not exists:
                db.add(Vlan(id=_next_vlan_id(db), vlan_number=mgmt_vlan, slice_id=sl.id, type="M"))
                db.flush()   # el próximo slice del loop necesita ver este id
                added += 1
        if added:
            db.commit()
        logger.info("[PLACEMENT] 🔒 Backfill de VLANs de gestión: %d reservada(s) para slices ya activos (%d revisados)",
                     added, len(slices))
    except Exception as exc:
        logger.error("[PLACEMENT] ❌ Backfill de VLANs de gestión falló: %s", exc, exc_info=True)
        db.rollback()
    finally:
        db.close()


VM_PLACEMENT_URL  = os.getenv("VM_PLACEMENT_URL",  "http://vm-placement:8080/placement")
OBSERVABILITY_URL = os.getenv("OBSERVABILITY_URL", "http://observability:8006")

# Umbrales de uso en vivo: workers que superen estos valores son excluidos
# como candidatos al placement (gate previo al solver).
MAX_CPU_USAGE_PCT = float(os.getenv("MAX_CPU_USAGE_PCT", "95"))
MAX_RAM_USAGE_PCT = float(os.getenv("MAX_RAM_USAGE_PCT", "95"))

# Factores de overcommit de arranque por dimensión (usados si Observabilidad
# aún no ha calculado OC_r[j] para el worker)
OC_CPU_DEFAULT   = 2.0
OC_RAM_DEFAULT   = 1.54   # 1/0.65
OC_DISCO_DEFAULT = 1.0    # sin overcommit

# Rango de VLANs válido en OpenStack (debe COINCIDIR con network_vlan_ranges
# del physnet en ml2_conf.ini, ej. physnet1:11:900). En OpenStack forzamos el
# segmentation_id de Neutron a estos números (opción 1: la tabla `vlans` = el
# tag real del cable), así que TODO (mgmt, C-VID, S-VID) debe caber aquí y ser
# único global. Linux Cluster usa su propio esquema (no toca estas variables).
OS_VLAN_MIN = int(os.getenv("OS_VLAN_MIN", "11"))
OS_VLAN_MAX = int(os.getenv("OS_VLAN_MAX", "900"))


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


def _fail_extend(db, db_slice, extend_info: dict) -> None:
    """
    Revierte una extensión fallida SIN tocar lo ya desplegado:
    borra las VMs nuevas de BD, quita los nodos/enlaces agregados del
    slice_json, libera IPs y devuelve el slice a ACTIVE.
    """
    import json as _json
    from app.models import IpPool
    new_names = set(extend_info.get("new_vm_names", []))
    new_edges = set(extend_info.get("new_edge_ids", []))

    new_vms = db.query(Vm).filter(Vm.slice_id == db_slice.id, Vm.name.in_(new_names)).all() if new_names else []
    for v in new_vms:
        if v.external_ip:
            rec = db.query(IpPool).filter(IpPool.ip_address == v.external_ip).first()
            if rec:
                rec.is_used = 0
                rec.vm_id = None
        db.delete(v)

    s_json = db_slice.slice_json or {}
    if isinstance(s_json, str):
        s_json = _json.loads(s_json)
    s_json["nodes"] = [n for n in s_json.get("nodes", []) if n.get("id") not in new_names]
    s_json["edges"] = [e for e in s_json.get("edges", []) if e.get("id") not in new_edges]
    s_json.pop("pending_extension", None)
    db_slice.slice_json = dict(s_json)
    db_slice.status = "ACTIVE"
    db.commit()
    logger.warning("[EXTEND] Extensión del slice %s revertida — el slice sigue ACTIVE.", db_slice.id)

async def process_placement_worker():
    """Worker asíncrono que procesa los despliegues uno por uno"""

    while True:
        request_data = await placement_queue.get()

        slice_id    = request_data["slice_id"]
        zone_id     = request_data["zone_id"]
        extend_info = request_data.get("extend")   # None = deploy normal
        db = SessionLocal()

        try:
            logger.info(f"[{slice_id}] Iniciando proceso de Placement...%s",
                        " (MODO EXTEND)" if extend_info else "")
            db_slice = db.query(Slice).filter(Slice.id == slice_id).first()

            if not db_slice:
                continue

            # ── 1. EXTRACCIÓN DE VMs DESDE BD ─────────────────────────────────
            all_vms_de_bd = db.query(Vm).filter(Vm.slice_id == slice_id).all()

            if extend_info:
                # Solo las VMs NUEVAS pasan por placement/deploy; las existentes
                # siguen corriendo y (si un enlace las toca) reciben hot-plug.
                new_names = set(extend_info.get("new_vm_names", []))
                vms_de_bd = [v for v in all_vms_de_bd if v.name in new_names]
            else:
                vms_de_bd = all_vms_de_bd

            if not vms_de_bd and not extend_info:
                db_slice.status = "FAILED"
                db.commit()
                continue

            # ── 1.5 RESOLUCIÓN DE IPs "random" (agnóstico Linux/OpenStack) ────
            # Ahora que la zona es conocida, cada VM con external_ip="random"
            # recibe una IP libre del pool de ESA zona, reservada atómicamente.
            from app.models import IpPool as _IpPool
            for vm in vms_de_bd:
                if vm.external_ip == "random":
                    libre = db.query(_IpPool).filter(
                        _IpPool.availability_zone_id == zone_id,
                        _IpPool.is_used == 0,
                    ).first()
                    if not libre:
                        logger.error("[PLACEMENT] ❌ Sin IPs libres en el pool de la zona %s para 'random'", zone_id)
                        vm.external_ip = None
                        vm.internet_access = 1  # queda con NAT saliente, sin IP entrante
                    else:
                        libre.is_used = 1
                        libre.vm_id = vm.id
                        vm.external_ip = libre.ip_address
                        logger.info("[PLACEMENT] 🎲 IP aleatoria asignada a VM %s (zona %s): %s",
                                    vm.name, zone_id, libre.ip_address)
            db.commit()

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
                if extend_info:
                    _fail_extend(db, db_slice, extend_info)
                else:
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
                    # Nombre físico del host para el BYOS (OpenStack: host de Nova).
                    # Con esto VMPlacement ya no necesita consultar a Nova.
                    "host_name":        w.name,
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
                if extend_info:
                    _fail_extend(db, db_slice, extend_info)
                else:
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

            if extend_info and not dynamic_vms:
                # Extensión con solo enlaces nuevos entre VMs existentes:
                # no hay nada que colocar, saltamos directo al enriquecimiento.
                placement_result = {"status": "SUCCESS", "placement_map": []}
            else:
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

                # En modo extend, solo se procesan los ENLACES NUEVOS
                if extend_info:
                    new_edge_ids = set(extend_info.get("new_edge_ids", []))
                    edges = [e for e in edges if e.get("id") in new_edge_ids]

                # El dict incluye TODAS las VMs (los enlaces nuevos pueden tocar existentes)
                vms_dict          = {vm.name: vm for vm in all_vms_de_bd}

                slice_hash         = hashlib.sha256(str(slice_id).encode()).hexdigest()
                mac_prefix         = f"52:54:00:{slice_hash[:2]}:{slice_hash[2:4]}"
                network_links      = []

                # ── Q-in-Q (802.1ad): S-VID por slice + C-VID reutilizable ────
                # El C-VID de cada enlace es el tag interno; el S-VID aísla el
                # slice. Con Q-in-Q los C-VIDs se REUTILIZAN entre slices (únicos
                # solo DENTRO del slice); el S-VID es único global. Sin Q-in-Q,
                # los C-VIDs siguen siendo únicos globales (single-tag, legado).
                qinq_enabled = os.getenv("QINQ_ENABLED", "false").lower() in ("1", "true", "yes")
                is_openstack = (zone_id == 2)

                # OpenStack (opción 1 — forzar segmentation_id en Neutron): mgmt,
                # C-VID y S-VID salen de un ÚNICO pool global [OS_VLAN_MIN,
                # OS_VLAN_MAX] (= network_vlan_ranges del physnet), todos únicos
                # entre sí, para que Neutron los acepte forzados y la tabla
                # `vlans` coincida 1:1 con el tag real del cable. Linux Cluster
                # conserva su esquema legado (no toca este pool).
                os_used = set()
                if is_openstack:
                    os_used = {r[0] for r in db.query(Vlan.vlan_number).filter(
                        Vlan.vlan_number.isnot(None)).all()}

                def _alloc_os_vlan():
                    for v in range(OS_VLAN_MIN, OS_VLAN_MAX + 1):
                        if v not in os_used:
                            os_used.add(v)
                            return v
                    raise RuntimeError(
                        f"Sin VLANs libres en el rango physnet [{OS_VLAN_MIN},{OS_VLAN_MAX}] (OpenStack)")

                s_vlan_id = 0
                if qinq_enabled:
                    # S-VID: reusar el del slice si ya existe (Modo Edición), o
                    # asignar uno nuevo único global.
                    existing_s = db.query(Vlan.vlan_number).filter(
                        Vlan.slice_id == slice_id, Vlan.type == "S",
                        Vlan.vlan_number.isnot(None),
                    ).first()
                    if existing_s:
                        s_vlan_id = existing_s[0]
                    else:
                        if is_openstack:
                            s_vlan_id = _alloc_os_vlan()
                        else:
                            s_base  = int(os.getenv("QINQ_SVID_BASE", "2"))
                            s_range = int(os.getenv("QINQ_SVID_RANGE", "4000"))
                            used_s  = {r[0] for r in db.query(Vlan.vlan_number).filter(
                                Vlan.type == "S", Vlan.vlan_number.isnot(None)).all()}
                            s_vlan_id = next((v for v in range(s_base, s_base + s_range) if v not in used_s), s_base)
                        # `vlans.id` no es AUTO_INCREMENT → id explícito con _next_vlan_id.
                        db.add(Vlan(id=_next_vlan_id(db), vlan_number=s_vlan_id, slice_id=slice_id, type="S"))
                        db.flush()
                    logger.info("[PLACEMENT] 🏷️  Q-in-Q ACTIVO — S-VID del slice %s = %d", slice_id, s_vlan_id)

                # Ocupación para C-VIDs (Linux). Las VLANs de gestión (type='M')
                # SIEMPRE ocupan el espacio global: el mgmt nunca viaja envuelto
                # en el túnel S-VID, así que un enlace de otro slice no debe
                # sortear ese número.
                vlans_ocupadas_mgmt = {r[0] for r in db.query(Vlan.vlan_number).filter(
                    Vlan.type == "M", Vlan.vlan_number.isnot(None)).all()}

                # C-VIDs ocupados: por-slice si Q-in-Q (reutilizables), global si no.
                if qinq_enabled:
                    vlans_ocupadas = list(vlans_ocupadas_mgmt | {r[0] for r in db.query(Vlan.vlan_number).filter(
                        Vlan.slice_id == slice_id, Vlan.type == "C",
                        Vlan.vlan_number.isnot(None)).all()})
                else:
                    vlans_ocupadas = list(vlans_ocupadas_mgmt | {v[0] for v in db.query(Vlan.id).all()})

                def obtener_vlan_libre():
                    # OpenStack: del pool global forzable en Neutron [OS_VLAN_MIN, OS_VLAN_MAX].
                    if is_openstack:
                        return _alloc_os_vlan()
                    # Linux: esquema legado (aleatorio 100-4000).
                    while True:
                        vid = random.randint(100, 4000)
                        if vid not in vlans_ocupadas:
                            vlans_ocupadas.append(vid)
                            return vid

                # VLAN de gestión del slice, reservada (idempotente) ANTES de
                # sortear los enlaces para que ningún otro slice choque con ella.
                # OpenStack: del pool (se FUERZA como segmentation_id del net-slice
                # en Neutron → tabla `vlans` = tag real). Linux: fórmula legado
                # 1000+slice_id (ya configurada físicamente en workers activos).
                if is_openstack:
                    existing_m = db.query(Vlan.vlan_number).filter(
                        Vlan.slice_id == slice_id, Vlan.type == "M",
                        Vlan.vlan_number.isnot(None)).first()
                    mgmt_vlan = existing_m[0] if existing_m else _alloc_os_vlan()
                else:
                    mgmt_vlan = 1000 + int(slice_id)
                    existing_m = db.query(Vlan.id).filter(
                        Vlan.slice_id == slice_id, Vlan.type == "M",
                        Vlan.vlan_number == mgmt_vlan).first()
                if not existing_m:
                    # `vlans.id` no es AUTO_INCREMENT → id explícito con _next_vlan_id.
                    db.add(Vlan(id=_next_vlan_id(db), vlan_number=mgmt_vlan, slice_id=slice_id, type="M"))
                    db.flush()
                if mgmt_vlan not in vlans_ocupadas:
                    vlans_ocupadas.append(mgmt_vlan)

                vms_payload_data   = {vm.name: {"tap_interfaces": []} for vm in vms_de_bd}

                # Hot-plug: taps nuevos destinados a VMs YA desplegadas (modo extend)
                hotplug_taps: dict = {}

                existing_deployed = slice_json.get("deployed_vms", []) if extend_info else []
                # El contador de MACs continúa después de las ya usadas por el slice
                global_mac_counter = sum(len(dv.get("tap_interfaces", [])) for dv in existing_deployed)

                # TAP de gestión para cada VM.
                # OJO: el nombre debe ser ÚNICO y ≤15 chars (IFNAMSIZ). Usamos el
                # id de BD de la VM (único global), NO el prefijo del vm_id — que
                # ya no es distintivo (todos los nodos de una sesión comparten
                # prefijo). Antes se usaba vm_id[:4] y colisionaba → "Device busy".
                for vm in vms_de_bd:
                    tap_mgmt = f"t-{vm.id}-m"
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

                    # Worker de cada extremo: del placement si la VM es nueva,
                    # o de la BD si ya está desplegada (modo extend).
                    worker1_id = next((w["worker_id"] for w in placement_map if w["vm_id"] == vm1_id),
                                      vms_dict[vm1_id].worker_id)
                    worker2_id = next((w["worker_id"] for w in placement_map if w["vm_id"] == vm2_id),
                                      vms_dict[vm2_id].worker_id)
                    worker1    = server_inventory.get(worker1_id, {})
                    worker2    = server_inventory.get(worker2_id, {})

                    # El VLAN es único por enlace (global) → base perfecta para el
                    # nombre del TAP. '-a'/'-b' distinguen los dos extremos. Único
                    # y ≤15 chars sin depender del prefijo del vm_id.
                    vlan_actual = obtener_vlan_libre()
                    tap1 = f"t-{vlan_actual}-a"
                    tap2 = f"t-{vlan_actual}-b"

                    mac1 = f"{mac_prefix}:{global_mac_counter:02x}".upper(); global_mac_counter += 1
                    mac2 = f"{mac_prefix}:{global_mac_counter:02x}".upper(); global_mac_counter += 1

                    # VM nueva → tap al arranque; VM existente → hot-plug vía QMP/Nova
                    for _vid, _tap in ((vm1_id, {"tap_name": tap1, "mac": mac1}),
                                       (vm2_id, {"tap_name": tap2, "mac": mac2})):
                        if _vid in vms_payload_data:
                            vms_payload_data[_vid]["tap_interfaces"].append(_tap)
                        else:
                            hotplug_taps.setdefault(_vid, []).append(_tap)

                    network_links.append({
                        "connection_id":       f"{vm1_id}-{vm2_id}-{vlan_actual}",
                        "vlan_id":             vlan_actual,     # C-VID (tag interno)
                        "s_vlan_id":           s_vlan_id,       # S-VID del slice (tag externo)
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

                    # C-VID en `vlan_number` (id auto-incremental) para OpenStack
                    # (es el segid REAL forzado en Neutron) y para Q-in-Q (permite
                    # reuso entre slices). Solo el Linux single-tag legado guarda
                    # el número en la PK (type='p2p').
                    # C-VID en `vlan_number` para OpenStack (es el segid REAL
                    # forzado en Neutron) y para Q-in-Q (permite reuso del número
                    # entre slices). `vlan_number` es el C-VID real; `id` es un PK
                    # sin significado — hay que asignarlo a mano (`vlans.id` no es
                    # AUTO_INCREMENT). Solo el Linux single-tag legado guarda el
                    # número real en la PK (type='p2p').
                    if is_openstack or qinq_enabled:
                        db.add(Vlan(id=_next_vlan_id(db), vlan_number=vlan_actual, slice_id=slice_id, type="C"))
                        db.flush()   # el siguiente edge del loop necesita ver este id
                    else:
                        db.add(Vlan(id=vlan_actual, slice_id=slice_id, type="p2p"))
                    logger.info(
                        "[PLACEMENT] 🔗 Enlace %-20s → %-20s | VLAN: %-4d | "
                        "TAP1: %-28s MAC1: %s | TAP2: %-28s MAC2: %s",
                        vm1_id, vm2_id, vlan_actual, tap1, mac1, tap2, mac2
                    )

                logger.info("[PLACEMENT] ✅ %d enlace(s) procesados: MACs y VLANs asignados", len(edges))

                # Solo cuentan como "ocupados" los puertos de VMs de slices
                # todavía vivos — el destroy nunca limpia `vnc_port` (solo
                # cambia `state`), así que sin este filtro cada VM que alguna
                # vez pasó por el worker bloquea su puerto para siempre. Con
                # solo 99 puertos (5901-5999) esto termina agotando el pool
                # y dejando `obtener_vnc_libre` sin ninguno disponible.
                vnc_por_worker = {}
                for w_id in server_inventory.keys():
                    vnc_ocupados_bd = db.query(Vm.vnc_port).filter(
                        Vm.worker_id == w_id,
                        Vm.vnc_port.isnot(None),
                        Vm.state.notin_(("TERMINATED", "FAILED")),
                    ).all()
                    vnc_por_worker[w_id] = set(v[0] for v in vnc_ocupados_bd)

                octeto_2        = (int(slice_id) // 256) % 256
                octeto_3        = int(slice_id) % 256
                # En extend, las IPs internas continúan después de las ya asignadas
                ip_host_counter = 10 + (len(existing_deployed) if extend_info else 0)

                # Llave pública SSH del dueño del slice (REQ-US-02):
                # se inyecta vía cloud-init en todas las VMs del despliegue.
                from app.models import UserSshKey
                owner_key_rec = db.query(UserSshKey).filter(
                    UserSshKey.user_id == db_slice.creator_id
                ).first()
                owner_ssh_key = owner_key_rec.public_key if owner_key_rec else ""
                if owner_ssh_key:
                    logger.info("[PLACEMENT] 🔑 Llave SSH del dueño encontrada — se inyectará en las VMs")

                # UUID Nova cacheado (si el flavor ya fue materializado — eager
                # por un admin, o lazy en un deploy previo) para que el CP no
                # tenga que re-listar/crear en Nova si ya sabemos cuál es.
                flavor_ids_usados = {vm.flavor_id for vm in vms_de_bd if vm.flavor_id}
                flavor_provider_map = {}
                if flavor_ids_usados:
                    flavor_provider_map = {
                        f_id: f_provider_id
                        for f_id, f_provider_id in db.query(Flavor.id, Flavor.provider_flavor_id)
                            .filter(Flavor.id.in_(flavor_ids_usados)).all()
                    }

                vms_payload = []
                for vm in vms_de_bd:
                    logger.info("[PLACEMENT] 🔧 Procesando VM: %s", vm.name)
                    worker_id   = next(w["worker_id"] for w in placement_map if w["vm_id"] == vm.name)
                    server_info = server_inventory.get(worker_id, {})
                    vm.worker_id = worker_id

                    if not vm.vnc_port:
                        # Límite de intentos: sin esto, si el rango (99 puertos)
                        # llegara a agotarse de verdad, este bucle gira para
                        # siempre sin ningún `await` — bloquea el event loop
                        # completo del proceso, no solo esta tarea.
                        ocupados = vnc_por_worker.setdefault(worker_id, set())
                        for _ in range(500):
                            candidato = random.randint(5901, 5999)
                            if candidato not in ocupados:
                                vm.vnc_port = candidato
                                ocupados.add(candidato)
                                logger.info("[PLACEMENT]    VNC asignado: VM %-20s → Worker-%d puerto %d",
                                            vm.name, worker_id, candidato)
                                break
                        else:
                            raise RuntimeError(
                                f"Sin puertos VNC libres en Worker-{worker_id} (rango 5901-5999 agotado)."
                            )

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
                        "provider_flavor_id": flavor_provider_map.get(vm.flavor_id),
                        "flavor_name":     vm.flavor_name,
                        "image_path":      img_path,
                        "vnc_port":        vm.vnc_port,
                        "vnc_display":     vm.vnc_port - 5900,
                        "tap_interfaces":  vms_payload_data[vm.name]["tap_interfaces"],
                        "internet_access": getattr(vm, 'internet_access', 0),
                        "external_ip":     vm.external_ip,
                        "internal_ip":     ip_interna_asignada,
                        "vm_user":         vm_user,
                        "vm_password":     vm_password,
                        "owner_ssh_public_key": owner_ssh_key,
                        "image_default_username": image_obj.default_username if image_obj else None,
                    })

                # ── 3.5 STUBS DE HOT-PLUG (modo extend) ────────────────────────
                # VMs existentes que reciben NICs nuevas: viajan al CP marcadas
                # con already_deployed=True; el CP no las lanza, solo conecta
                # las interfaces en caliente (QMP en Linux / Nova en OpenStack).
                for hp_vm_name, hp_taps in hotplug_taps.items():
                    hp_vm = vms_dict.get(hp_vm_name)
                    if not hp_vm:
                        continue
                    hp_worker = server_inventory.get(hp_vm.worker_id, {})
                    vms_payload.append({
                        "vm_id":           hp_vm_name,
                        "already_deployed": True,
                        "provider_instance_id": getattr(hp_vm, "provider_instance_id", None),
                        "worker_ip":       hp_worker.get("ip", "0.0.0.0"),
                        "worker_port":     hp_worker.get("port", 22),
                        "ssh_user":        hp_worker.get("user", "ubuntu"),
                        "ssh_private_key": get_ssh_key(hp_worker.get("key_path", "")),
                        "vcpus":           int(hp_vm.vcore or 1),
                        "ram_mb":          float(hp_vm.ram or 512),
                        "disk_gb":         float(hp_vm.disk or 5),
                        "image_path":      "",
                        "vnc_port":        hp_vm.vnc_port,
                        "vnc_display":     (hp_vm.vnc_port - 5900) if hp_vm.vnc_port else None,
                        "tap_interfaces":  hp_taps,   # SOLO las NICs a conectar en caliente
                        "internal_ip":     "0.0.0.0",
                    })
                    logger.info("[EXTEND] 🔌 VM existente '%s' recibirá %d NIC(s) por hot-plug",
                                hp_vm_name, len(hp_taps))

                # ── 4. PUBLICACIÓN EN NATS ─────────────────────────────────────
                logger.info("="*70)
                logger.info("[PLACEMENT] 📤 Publicando en NATS → QueueManager%s",
                            " (EXTEND)" if extend_info else "")
                logger.info("[PLACEMENT]    slice_id=%s  VMs=%d  links=%d",
                            slice_id, len(vms_payload), len(network_links))

                queue_manager_payload = {
                    "slice_id":             str(slice_id),
                    "request_id":           f"req-{uuid.uuid4().hex[:8]}",
                    "availability_zone_id": zone_id,
                    "mode":                 "extend" if extend_info else "deploy",
                    "vms":                  vms_payload,
                    "links":                network_links,
                    "workers":              servers_state,
                    "mgmt_vlan":            mgmt_vlan,
                }

                # Q-in-Q en OpenStack: mapa SSH de los computes (keyed por
                # Worker.name == host de Nova). El NetworkOrchestrator lo usa,
                # junto al host_map y a los s_vlan_id de los enlaces, para
                # interponer el dot1q-tunnel en el compute de cada VM ANTES de
                # que Compute la cree (mismo paso que en Linux Cluster).
                if qinq_enabled and zone_id == 2 and s_vlan_id:
                    queue_manager_payload["compute_ssh_map"] = {
                        w.name: {
                            "ip":   w.ip,
                            "port": getattr(w, "ssh_port", 22),
                            "user": getattr(w, "ssh_user", None),
                            "key":  get_ssh_key(getattr(w, "ssh_key_path", "")),
                        }
                        for w in workers_zona if w.name
                    }
                    logger.info("[PLACEMENT] 🏷️  Q-in-Q OpenStack: S-VID %d, %d computes en el mapa SSH",
                                s_vlan_id, len(queue_manager_payload["compute_ssh_map"]))

                slice_json = db_slice.slice_json
                if isinstance(slice_json, str):
                    slice_json = json.loads(slice_json)
                if not slice_json:
                    slice_json = {}

                if extend_info:
                    # MERGE: lo existente se conserva; se agregan las VMs nuevas
                    # (no los stubs) y los enlaces nuevos. Las VMs con hot-plug
                    # suman sus taps nuevos (necesario para el destroy futuro).
                    new_real_vms = [v for v in vms_payload if not v.get("already_deployed")]
                    merged_vms = slice_json.get("deployed_vms", []) + new_real_vms
                    for dv in merged_vms:
                        if dv.get("vm_id") in hotplug_taps and not any(
                            t.get("tap_name") == ht.get("tap_name")
                            for t in dv.get("tap_interfaces", []) for ht in hotplug_taps[dv["vm_id"]]
                        ):
                            dv.setdefault("tap_interfaces", []).extend(hotplug_taps[dv["vm_id"]])
                    slice_json["deployed_vms"]   = merged_vms
                    slice_json["deployed_links"] = slice_json.get("deployed_links", []) + network_links
                else:
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
                    db.commit()
                elif extend_info:
                    logger.error("[PLACEMENT] ❌ Fallo al publicar extensión en NATS — revirtiendo")
                    _fail_extend(db, db_slice, extend_info)
                else:
                    db_slice.status = "FAILED"
                    logger.error("[PLACEMENT] ❌ Fallo al publicar en NATS, estado → FAILED")
                    db.commit()
                logger.info("[PLACEMENT] 🏁 Flujo de placement completado para slice=%s", slice_id)
                logger.info("="*70)

            else:
                if extend_info:
                    logger.error("[EXTEND] Placement FAILED para la extensión — revirtiendo")
                    _fail_extend(db, db_slice, extend_info)
                else:
                    db_slice.status = "FAILED"
                    db.commit()

        except Exception as e:
            logger.error(f"[{slice_id}] Error procesando placement: {str(e)}", exc_info=True)
            db.rollback()
            if 'db_slice' in locals() and db_slice:
                try:
                    if extend_info:
                        _fail_extend(db, db_slice, extend_info)
                    else:
                        db_slice.status = "FAILED"
                        db.commit()
                except Exception as rollback_err:
                    logger.error(f"[{slice_id}] No se pudo revertir/actualizar el slice: {rollback_err}")
        finally:
            db.close()
            placement_queue.task_done()
