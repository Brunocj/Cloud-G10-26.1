import json
import logging
import uuid # <-- Añadir para el request_id del destroy
from app.database import SessionLocal
from app.models import Slice, Vlan
from app.nats_producer import nats_producer

logger = logging.getLogger("SliceManager.Listener")

async def nats_result_listener():
    await nats_producer.connect()
    
    async def message_handler(msg):
        data = json.loads(msg.data.decode())
        slice_id = int(data.get("slice_id")) 
        status = data.get("status")
        
        db = SessionLocal()
        try:
            db_slice = db.query(Slice).filter(Slice.id == slice_id).first()
            if db_slice:
                # 🔥 FIX: Si ya está en estado terminal (por Rollback o Destroy), IGNORAMOS los success tardíos
                if db_slice.status in ["TERMINATED", "FAILED"]:
                    logger.info(f"Ignorando mensaje '{status}' porque el slice ya está en {db_slice.status}.")
                elif status.lower() == "success":
                    db_slice.status = "ACTIVE"
                    logger.info(f"✅ Topología {slice_id} desplegada exitosamente (ACTIVE).")
                else:
                    # 🔥 LÓGICA DE ROLLBACK AUTOMÁTICO
                    logger.warning(f"⚠️ Topología {slice_id} reportó estado '{status}'. Iniciando Rollback...")
                    db_slice.status = "FAILED"
                    # ... (resto de tu lógica de rollback)
                    
                    # 1. Liberamos las VLANs de la base de datos local
                    db.query(Vlan).filter(Vlan.slice_id == slice_id).delete()
                    
                    # 2. Disparamos la orden de destrucción a NATS para limpiar los workers
                    rollback_payload = {
                        "slice_id": str(slice_id), 
                        "request_id": f"req-rollback-{uuid.uuid4().hex[:8]}"
                    }
                    await nats_producer.publish_destroy(rollback_payload)
                    logger.info(f"🧹 Orden de limpieza (Rollback) enviada para el slice {slice_id}.")
                
                db.commit()
        except Exception as e:
            logger.error(f"❌ Error actualizando BD desde NATS: {e}")
        finally:
            db.close()

    await nats_producer.nc.subscribe("slice.result", cb=message_handler)
    logger.info("📡 Listener de resultados NATS iniciado...")