import json
import logging
import uuid # <-- Añadir para el request_id del destroy
from app.database import SessionLocal
from app.models import Slice, Vlan, Vm, IpPool
from app.nats_producer import nats_producer

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
                    vms_updated = db.query(Vm).filter(Vm.slice_id == slice_id).update({"state": "ACTIVE"})
                    logger.info("[LISTENER] 🟢 Slice %s → ACTIVE  (%d VMs actualizadas)", slice_id, vms_updated)
                    logger.info("[LISTENER] ✅ Despliegue completado exitosamente")
                else:
                    logger.warning("[LISTENER] ⚠️  Estado '%s' recibido para slice %s. Iniciando Rollback...",
                                   status, slice_id)
                    db_slice.status = "FAILED"

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
                    rollback_payload = {
                        "slice_id": str(slice_id),
                        "request_id": f"req-rollback-{uuid.uuid4().hex[:8]}"
                    }
                    await nats_producer.publish_destroy(rollback_payload)
                    logger.info("[LISTENER] 🧹 Orden de limpieza (Rollback) enviada para slice %s → FAILED", slice_id)

                db.commit()
                logger.info("[LISTENER] 💾 BD actualizada correctamente")
            else:
                logger.warning("[LISTENER] ⚠️  Slice %s no encontrado en BD, descartando resultado", slice_id)
        except Exception as e:
            logger.error("[LISTENER] ❌ Error actualizando BD desde NATS: %s", e)
        finally:
            db.close()
            logger.info("="*70)


    await nats_producer.nc.subscribe("slice.result", cb=message_handler)
    logger.info("📡 Listener de resultados NATS iniciado...")