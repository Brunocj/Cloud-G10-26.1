from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Topology
from app.schemas import DeployRequest
from app.services.placement_worker import placement_queue
from app.nats_producer import nats_producer
import uuid

router = APIRouter(prefix="/api/v1/topologies", tags=["Deploy"])

@router.post("/{topology_id}/deploy", status_code=202)
async def request_deploy(topology_id: int, request: DeployRequest, db: Session = Depends(get_db)):
    db_topology = db.query(Topology).filter(Topology.id == topology_id).first()
    if not db_topology:
        raise HTTPException(status_code=404, detail="Topología no encontrada")
        
    db_topology.status = "PENDING_APPROVAL"
    db_topology.TTL = request.ttl_hours
    db.commit()

    # Encolamos (Usamos "topology_id" internamente)
    await placement_queue.put({"topology_id": topology_id, "zone": request.availability_zone})
    return {"status": "ACCEPTED", "message": "Enviado a validación de recursos."}

@router.delete("/{topology_id}", status_code=202)
async def request_destroy(topology_id: int, db: Session = Depends(get_db)):
    db_topology = db.query(Topology).filter(Topology.id == topology_id).first()
    if not db_topology or db_topology.status in ["DRAFT", "TERMINATED"]:
        raise HTTPException(status_code=400, detail="No se puede destruir en este estado.")

    payload = {
        "slice_id": str(topology_id), # Enviamos slice_id por red para QueueManager
        "request_id": f"req-destroy-{uuid.uuid4().hex[:8]}"
    }
    published = await nats_producer.publish_destroy(payload)
    
    if published:
        db_topology.status = "TERMINATED"
        db.commit()
        return {"status": "ACCEPTED"}
    raise HTTPException(status_code=500, detail="Error enviando orden a NATS")