from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models import Slice, Vm, Vlan, IpPool
from app.schemas import DeployRequest
from app.services.placement_worker import placement_queue
from app.nats_producer import nats_producer
import uuid
import json
import logging

router = APIRouter(prefix="/api/v1/slices", tags=["Deploy"])
logger = logging.getLogger("SliceManager.Deploy")


def _release_external_ips(db: Session, slice_id: int) -> None:
    vms = db.query(Vm).filter(
        Vm.slice_id == slice_id,
        Vm.external_ip.isnot(None)
    ).all()
    if not vms:
        return

    ips = [vm.external_ip for vm in vms if vm.external_ip]
    if not ips:
        return

    ip_records = db.query(IpPool).filter(IpPool.ip_address.in_(ips)).all()
    for record in ip_records:
        record.is_used = 0
        record.vm_id = None

@router.post("/{slice_id}/deploy", status_code=202)
async def request_deploy(slice_id: int, request: DeployRequest, db: Session = Depends(get_db)):
    logger.info("="*70)
    logger.info("[DEPLOY] 📥 Solicitud de despliegue recibida para slice_id=%s", slice_id)
    logger.info("[DEPLOY]    zona=%s  TTL=%sh  motivo=%s", request.availability_zone_id, request.ttl_hours, getattr(request, 'motivo', 'N/A'))
    db_slice = db.query(Slice).filter(Slice.id == slice_id).first()
    if not db_slice:
        raise HTTPException(status_code=404, detail="Slice no encontrada")

    logger.info("[DEPLOY] ✅ Slice '%s' encontrado en BD (estado actual: %s)", db_slice.name, db_slice.status)
    
    
    db_slice.status = "PENDING_APPROVAL"
    db_slice.TTL = request.ttl_hours
    db.commit()
    logger.info("[DEPLOY] 🟡 Estado cambiado a PENDING_APPROVAL")

    # Encolamos (Usamos "slice_id" internamente)
    await placement_queue.put({"slice_id": slice_id, "zone_id": request.availability_zone_id})
    logger.info("[DEPLOY] 📤 Solicitud encolada en placement_queue → worker en background la procesará")
    logger.info("="*70)
    return {"status": "ACCEPTED", "message": "Enviado a validación de recursos."}

@router.delete("/{slice_id}", status_code=202)
async def request_destroy(slice_id: int, db: Session = Depends(get_db)):
    db_slice = db.query(Slice).filter(Slice.id == slice_id).first()
    
    if not db_slice:
        raise HTTPException(status_code=404, detail="Slice no encontrado")
        
    if db_slice.status == "TERMINATED":
        raise HTTPException(status_code=400, detail="El slice ya está destruido.")

    # 🔥 NUEVA LÓGICA: Si es un borrador, solo lo borramos de MySQL y ya está.
    if db_slice.status == "DRAFT":
        _release_external_ips(db, slice_id)
        # Primero borramos las VMs hijas por la relación de llave foránea
        db.query(Vm).filter(Vm.slice_id == slice_id).delete()
        # Luego borramos el Slice
        db.delete(db_slice)
        db.commit()
        return {"status": "DELETED", "message": "Borrador eliminado de la base de datos."}

    db.query(Vlan).filter(Vlan.slice_id == slice_id).delete()
    # Si está ACTIVO o PROVISIONING, sí hay que avisarle a NATS que destruya todo
    # 1. Recuperamos la receta desde la base de datos
    s_json = db_slice.slice_json
    if isinstance(s_json, str):
        s_json = json.loads(s_json)
    if not s_json:
        s_json = {}
    # 2. Armamos el payload con TODO el detalle (VMs y Links)
    payload = {
        "slice_id": str(slice_id),
        "request_id": f"req-destroy-{uuid.uuid4().hex[:8]}",
        "vms": s_json.get("deployed_vms", []),     # 🔥 El Compute Provisioner necesita esto
        "links": s_json.get("deployed_links", [])  # 🔥 El Network Orchestrator necesita esto
    }
    
    # 3. Publicamos la destrucción
    published = await nats_producer.publish_destroy(payload)
    
    if published:
        # ... dentro del if que maneja la destrucción (ACTIVE, FAILED, etc) ...
        db_slice.status = "TERMINATED"
        
        # 🔥 FIX: Propagamos el estado a todas las VMs que pertenecen a este slice
        db.query(Vm).filter(Vm.slice_id == slice_id).update({"state": "TERMINATED"})

        # 🔥 Liberamos IPs externas del pool
        _release_external_ips(db, slice_id)
        
        db.commit()
        return {"status": "ACCEPTED", "message": "Orden de destrucción enviada."}
        
    raise HTTPException(status_code=500, detail="Error enviando orden a NATS")