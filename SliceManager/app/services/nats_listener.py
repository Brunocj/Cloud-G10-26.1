import json
import logging
from app.database import SessionLocal
from app.models import Topology
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
            db_slice = db.query(Topology).filter(Topology.id == slice_id).first()
            if db_slice:
                if status == "success" and db_slice.status != "TERMINATED":
                    db_slice.status = "ACTIVE"
                else:
                    db_slice.status = "FAILED"
                db.commit()
        except Exception as e:
            logger.error(f"Error actualizando BD desde NATS: {e}")
        finally:
            db.close()

    await nats_producer.nc.subscribe("slice.result", cb=message_handler)