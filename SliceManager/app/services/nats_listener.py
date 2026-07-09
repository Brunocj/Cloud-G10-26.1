import json
import logging
import os
import uuid
from datetime import datetime
from app.database import SessionLocal
from app.models import Slice, Vlan, Vm, IpPool, Worker
from app.nats_producer import nats_producer
from app.services.notification_hub import notification_hub

logger = logging.getLogger("SliceManager.Listener")

async def nats_result_listener():
    await nats_producer.connect()
    
    async def message_handler(msg):
        data = json.loads(msg.data.decode())
        slice_id = int(data.get("slice_id"))
        status = data.get("status")

        logger.info("="*70)
        logger.info("[LISTENER] 📨 Resultado recibido desde QueueManager vía NATS")
        logger.info("[LISTENER]    slice_id=%s  status=%s", slice_id, status)
        logger.info("[LISTENER]    vms en payload: %s", data.get("vms"))

        db = SessionLocal()
        try:
            db_slice = db.query(Slice).filter(Slice.id == slice_id).first()
            if db_slice:
                logger.info("[LISTENER]    Estado actual en BD: %s", db_slice.status)
                # Si ya está en estado terminal (por Rollback o Destroy), IGNORAMOS los success tardíos
                if db_slice.status in ["TERMINATED", "FAILED"]:
                    logger.info("[LISTENER] ⏭️  Ignorando resultado '%s': slice ya en estado %s",
                                status, db_slice.status)
                elif status.lower() == "success":
                    db_slice.status = "ACTIVE"
                    db_slice.date_deployed = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    vms_updated = db.query(Vm).filter(Vm.slice_id == slice_id).update({"state": "ACTIVE"})
                    logger.info("[LISTENER] 🟢 Slice %s → ACTIVE  (%d VMs actualizadas)", slice_id, vms_updated)

                    # Persistir vnc_url y provider_instance_id en columnas SQL y en slice_json
                    result_vms = data.get("vms", [])
                    if result_vms:
                        vnc_url_map            = {}
                        provider_instance_map  = {}
                        external_ip_map        = {}
                        for v in result_vms:
                            vid = v.get("vm_id")
                            if v.get("vnc_url"):
                                vnc_url_map[vid] = v["vnc_url"]
                            if v.get("provider_instance_id"):
                                provider_instance_map[vid] = v["provider_instance_id"]
                            if v.get("external_ip"):
                                external_ip_map[vid] = v["external_ip"]

                        # Actualizar columnas SQL de cada VM
                        db_vms = db.query(Vm).filter(Vm.slice_id == slice_id).all()
                        for db_vm in db_vms:
                            if db_vm.name in vnc_url_map:
                                db_vm.vnc_url = vnc_url_map[db_vm.name]
                                logger.info("[LISTENER] 🖥️  vnc_url guardado para VM %s: %s…",
                                            db_vm.name, vnc_url_map[db_vm.name][:8])
                            if db_vm.name in provider_instance_map:
                                db_vm.provider_instance_id = provider_instance_map[db_vm.name]
                                logger.info("[LISTENER] 🔑 provider_instance_id guardado para VM %s: %s",
                                            db_vm.name, provider_instance_map[db_vm.name])
                            if db_vm.name in external_ip_map:
                                db_vm.external_ip = external_ip_map[db_vm.name]
                                logger.info("[LISTENER] 🌐 external_ip guardado para VM %s: %s",
                                            db_vm.name, external_ip_map[db_vm.name])

                        # Actualizar también slice_json["deployed_vms"]
                        s_json = db_slice.slice_json or {}
                        if isinstance(s_json, str):
                            s_json = json.loads(s_json)
                        deployed_vms = s_json.get("deployed_vms", [])
                        for vm_entry in deployed_vms:
                            vid = vm_entry.get("vm_id")
                            if vid in vnc_url_map:
                                vm_entry["vnc_url"] = vnc_url_map[vid]
                            if vid in provider_instance_map:
                                vm_entry["provider_instance_id"] = provider_instance_map[vid]
                            if vid in external_ip_map:
                                vm_entry["external_ip"] = external_ip_map[vid]
                        s_json["deployed_vms"] = deployed_vms
                        db_slice.slice_json = dict(s_json)

                    logger.info("[LISTENER] ✅ Despliegue completado exitosamente")
                else:
                    logger.warning("[LISTENER] ⚠️  Estado '%s' recibido para slice %s. Iniciando Rollback...",
                                   status, slice_id)
                    db_slice.status = "FAILED"
                    db.query(Vm).filter(Vm.slice_id == slice_id).update({"state": "FAILED"})

                    # 1. Liberamos las VLANs de la base de datos local
                    vlans_deleted = db.query(Vlan).filter(Vlan.slice_id == slice_id).delete()
                    logger.info("[LISTENER]    Rollback: %d VLANs liberadas", vlans_deleted)

                    # 2. Liberamos IPs externas del pool
                    vms = db.query(Vm).filter(
                        Vm.slice_id == slice_id,
                        Vm.external_ip.isnot(None)
                    ).all()
                    if vms:
                        ips = [vm.external_ip for vm in vms if vm.external_ip]
                        if ips:
                            ip_records = db.query(IpPool).filter(IpPool.ip_address.in_(ips)).all()
                            for record in ip_records:
                                record.is_used = 0
                                record.vm_id = None
                            logger.info("[LISTENER]    Rollback: %d IPs externas liberadas", len(ip_records))

                    # 3. Disparamos la orden de destrucción a NATS para limpiar los workers
                    # Incluimos vms/links con claves SSH para que CP/NO no fallen con puerto 22
                    s_json = db_slice.slice_json or {}
                    if isinstance(s_json, str):
                        s_json = json.loads(s_json)
                    all_workers = db.query(Worker).all()
                    workers_by_ip = {w.ip: w for w in all_workers}
                    any_worker = next(iter(workers_by_ip.values()), None)
                    fresh_key = ""
                    if any_worker and any_worker.ssh_key_path:
                        key_path = any_worker.ssh_key_path
                        for p in [key_path, f"/app/keys/{os.path.basename(key_path)}"]:
                            if os.path.exists(p):
                                try:
                                    fresh_key = open(p).read()
                                    break
                                except Exception:
                                    pass
                    deployed_vms   = s_json.get("deployed_vms", [])
                    deployed_links = s_json.get("deployed_links", [])
                    if fresh_key:
                        for vm in deployed_vms:
                            vm["ssh_private_key"] = fresh_key
                        for link in deployed_links:
                            for side in ("vm1", "vm2"):
                                link[f"{side}_ssh_private_key"] = fresh_key
                    rollback_payload = {
                        "slice_id":             str(slice_id),
                        "request_id":           f"req-rollback-{uuid.uuid4().hex[:8]}",
                        "availability_zone_id": db_slice.availability_zone_id or 1,
                        "vms":                  deployed_vms,
                        "links":                deployed_links,
                    }
                    await nats_producer.publish_destroy(rollback_payload)
                    logger.info("[LISTENER] 🧹 Orden de limpieza (Rollback) enviada para slice %s → FAILED", slice_id)

                db.commit()
                logger.info("[LISTENER] 💾 BD actualizada correctamente")

                # Bitácora del resultado del orquestador
                from app.services.audit import audit
                if db_slice.status == "ACTIVE":
                    audit("system", "system", "Orchestrator", "slice_active",
                          f"Slice '{db_slice.name}' desplegado correctamente.",
                          slice_id=slice_id, project_id=db_slice.project_id)
                elif db_slice.status == "FAILED":
                    audit("system", "system", "Orchestrator", "deploy_failed",
                          f"Despliegue del slice '{db_slice.name}' falló (status={status}).",
                          level="ERROR", slice_id=slice_id, project_id=db_slice.project_id)

                # Notificación en tiempo real al dueño del slice
                try:
                    if db_slice.status == "ACTIVE":
                        await notification_hub.notify_user(db_slice.creator_id, {
                            "type":     "slice_active",
                            "slice_id": slice_id,
                            "title":    "Slice desplegado",
                            "message":  f"Tu slice \"{db_slice.name}\" está ACTIVO.",
                        })
                    elif db_slice.status == "FAILED":
                        await notification_hub.notify_user(db_slice.creator_id, {
                            "type":     "slice_failed",
                            "slice_id": slice_id,
                            "title":    "Despliegue fallido",
                            "message":  f"El despliegue de \"{db_slice.name}\" falló. Revisa los recursos e intenta de nuevo.",
                        })
                except Exception as notify_exc:
                    logger.warning("[LISTENER] No se pudo notificar por WS: %s", notify_exc)
            else:
                logger.warning("[LISTENER] ⚠️  Slice %s no encontrado en BD, descartando resultado", slice_id)
        except Exception as e:
            logger.error("[LISTENER] ❌ Error actualizando BD desde NATS: %s", e)
        finally:
            db.close()
            logger.info("="*70)


    await nats_producer.nc.subscribe("slice.result", cb=message_handler)
    logger.info("📡 Listener de resultados NATS iniciado...")