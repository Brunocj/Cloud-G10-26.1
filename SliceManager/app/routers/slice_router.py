# app/routers/slice_router.py
from app.repositories.slice_repo import SliceRepository
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas import DraftSaveRequest
from app.models import Slice, Vm, Image, Worker
import json

router = APIRouter(prefix="/api/v1/slices", tags=["Slices / Topologies"])

@router.get("/", status_code=200)
def list_slices(db: Session = Depends(get_db)):
    slices = db.query(Slice).filter(Slice.creator_id == "user-123").all()
    
    result = []
    for t in slices:
        s_json = t.slice_json if t.slice_json else {}
        if isinstance(s_json, str):
            s_json = json.loads(s_json)
            
        nodes = s_json.get("nodes", [])
        edges = s_json.get("edges", [])
        
        result.append({
            "id": t.id,
            # 🔥 FIX: Usamos el nombre real de la BD. Si por algún error está vacío, usamos un fallback.
            "name": t.name if t.name else f"Slice {t.id}", 
            "status": t.status,
            "nodeCount": len(nodes),
            "edgeCount": len(edges),
            "vcpus": sum(int(n.get("vcores", 0)) for n in nodes),
            "ramLabel": f"{sum(float(n.get('ram', 0)) for n in nodes)} MB",
            "nodes": nodes, 
            "edges": edges
        })
    return result

@router.post("/draft")
def create_draft(request: DraftSaveRequest, db: Session = Depends(get_db)):
    try:
        usuario_actual_id = "user-123" 
        
        slice_creado = SliceRepository.save_draft(
            db=db, 
            owner_id=usuario_actual_id, 
            payload=request.slice_json,
            request_name=request.name  
        )
        
        workers_db = db.query(Worker).all()
        num_workers = len(workers_db)
        
        nodes_list = request.slice_json.get("nodes", [])
        
        for index, vm_data in enumerate(nodes_list):
            asignado = workers_db[index % num_workers] if num_workers > 0 else None
            
            nueva_vm = Vm(
                name=vm_data.get("id"), # 🔥 FIX: DEBE ser 'id' (ej. n214) para que la red no explote
                vcore=int(vm_data.get("vcores", 1)),
                ram=float(vm_data.get("ram", 512.0)), # 🔥 FIX: float para tu DB double
                disk=float(vm_data.get("disk", 5.0)), # 🔥 FIX: float para tu DB double
                state="DRAFT",
                slice_id=slice_creado.id,
                image_id=vm_data.get("image_id"),
                worker_id=asignado.id if asignado else None
            )
            
            vm_data["worker"] = asignado.name if asignado else "Unassigned"
            vm_data["worker_id"] = asignado.id if asignado else None
            
            db.add(nueva_vm)
        
        slice_creado.slice_json = {"nodes": nodes_list, "edges": request.slice_json.get("edges", [])}
            
        db.commit()
        
        return {
            "message": "Borrador guardado con éxito", 
            "slice_id": slice_creado.id
        }
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.put("/{slice_id}/draft", status_code=200)
def update_draft(slice_id: int, request: DraftSaveRequest, db: Session = Depends(get_db)):
    db_slice = db.query(Slice).filter(Slice.id == slice_id).first()
    if not db_slice:
        raise HTTPException(status_code=404, detail="Slice no encontrado")
    
    if db_slice.status != "DRAFT":
        raise HTTPException(status_code=400, detail="Solo se pueden editar borradores")

    db_slice.slice_json = {"edges": request.slice_json.get("edges", [])}
    
    db.query(Vm).filter(Vm.slice_id == slice_id).delete()
    
    for vm_data in request.slice_json.get("nodes", []):
        nueva_vm = Vm(
            name=vm_data.get("id"),
            vcore=int(vm_data.get("vcores", 1)),
            ram=float(vm_data.get("ram", 512.0)),
            disk=float(vm_data.get("disk", 5.0)),
            state="DRAFT",
            slice_id=slice_id,
            external_ip=vm_data.get("external_ip", None),
            image_id=vm_data.get("image_id"),  # 🔥 FIX: ¡No olvides recuperar la imagen!
            worker_id=vm_data.get("worker_id") # 🔥 FIX: ¡No olvides recuperar el worker asignado!
        )
        db.add(nueva_vm)

    db.commit()
    return {"message": "Borrador actualizado con éxito"}

@router.get("/utils/images", status_code=200)
def get_available_images(db: Session = Depends(get_db)):
    imagenes = db.query(Image).all()
    if not imagenes:
        return [{"id": 0, "name": "Ubuntu 22.04 LTS (Fallback)"}]
    
    return [{"id": img.id, "name": img.name} for img in imagenes]

@router.get("/utils/workers", status_code=200)
def get_available_workers(db: Session = Depends(get_db)):
    workers = db.query(Worker).all()
    if not workers:
        return ["server1"]
    return [w.name for w in workers]