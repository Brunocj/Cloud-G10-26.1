# app/routers/slice_router.py
from app.repositories.slice_repo import SliceRepository
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas import DraftSaveRequest
from app.models import Slice, Vm, Image, Worker, IpPool
import json

router = APIRouter(prefix="/api/v1/slices", tags=["Slices / Topologies"])

def _calcular_peso(vcpus: int, ram_mb: float, disk_gb: float) -> float:
    """w = 3·vcpus + 5·ram_gb + 1·disk_gb"""
    ram_gb = ram_mb / 1024.0
    return round(3 * vcpus + 5 * ram_gb + 1 * disk_gb, 4)

@router.get("/", status_code=200)
def list_slices(db: Session = Depends(get_db)):
    slices = db.query(Slice).filter(Slice.creator_id == "user-123").order_by(Slice.id.desc()).all()
    
    result = []
    for t in slices:
        s_json = t.slice_json if t.slice_json else {}
        if isinstance(s_json, str):
            s_json = json.loads(s_json)
            
        nodes = s_json.get("nodes", [])
        edges = s_json.get("edges", [])
        
        # 🔥 FIX: Extraemos la información física de las VMs desplegadas
        deployed_vms = s_json.get("deployed_vms", [])
        
        # 🔥 FIX: Fusionamos la IP y el VNC Port dentro de los nodos del frontend
        if deployed_vms:
            for node in nodes:
                for d_vm in deployed_vms:
                    if node.get("id") == d_vm.get("vm_id"):
                        node["worker_ip"] = d_vm.get("worker_ip")
                        node["vnc_port"] = d_vm.get("vnc_port")
        
        result.append({
            "id": t.id,
            "name": t.name if t.name else f"Slice {t.id}", 
            "status": t.status,
            "nodeCount": len(nodes),
            "edgeCount": len(edges),
            "vcpus": sum(int(n.get("vcores", 0)) for n in nodes),
            "ramLabel": f"{sum(float(n.get('ram', 0)) for n in nodes)} MB",
            "nodes": nodes, # Ahora estos nodos SÍ llevan la IP y el puerto
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
            
            # 🔥 LÓGICA DE SEGURIDAD R5
            ext_ip = vm_data.get("external_ip", None)
            # Si tiene IP externa, forzamos internet=1. Si no, tomamos lo que mande el frontend (o 0)
            int_access = 1 if ext_ip else int(vm_data.get("internet_access", 0))
            
            vcpus_val = int(vm_data.get("vcores", 1))
            ram_val   = float(vm_data.get("ram", 512.0))
            disk_val  = float(vm_data.get("disk", 5.0))
            peso_val  = _calcular_peso(vcpus_val, ram_val, disk_val)

            nueva_vm = Vm(
                name=vm_data.get("id"),
                vcore=vcpus_val,
                ram=ram_val,
                disk=disk_val,
                state="DRAFT",
                slice_id=slice_creado.id,
                image_id=vm_data.get("image_id"),
                worker_id=asignado.id if asignado else None,
                external_ip=ext_ip,
                internet_access=int_access,
                peso=peso_val,
                peso_actualizado=peso_val
            )
            
            vm_data["worker"] = asignado.name if asignado else "Unassigned"
            vm_data["worker_id"] = asignado.id if asignado else None
            
            db.add(nueva_vm)

            db.flush() # 🔥 Sincroniza temporalmente para obtener el ID de la VM

            # 🔥 Ocupar la IP en la tabla IpPool
            if ext_ip:
                ip_record = db.query(IpPool).filter(IpPool.ip_address == ext_ip, IpPool.is_used == 0).first()
                if ip_record:
                    ip_record.is_used = 1
                    ip_record.vm_id = nueva_vm.id
                else:
                    # Opcional: Lanzar error si alguien mandó una IP inválida o ya en uso
                    pass
        
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
    
    # 🔥 Liberar IPs del pool antes de destruir las VMs viejas
    vms_antiguas = db.query(Vm).filter(Vm.slice_id == slice_id).all()
    for v_ant in vms_antiguas:
        if v_ant.external_ip:
            ip_record = db.query(IpPool).filter(IpPool.ip_address == v_ant.external_ip).first()
            if ip_record:
                ip_record.is_used = 0
                ip_record.vm_id = None

    # Limpiamos VMs previas
    db.query(Vm).filter(Vm.slice_id == slice_id).delete()
    
    for vm_data in request.slice_json.get("nodes", []):
        
        # 🔥 LÓGICA DE SEGURIDAD R5
        ext_ip = vm_data.get("external_ip", None)
        int_access = 1 if ext_ip else int(vm_data.get("internet_access", 0))

        vcpus_val = int(vm_data.get("vcores", 1))
        ram_val   = float(vm_data.get("ram", 512.0))
        disk_val  = float(vm_data.get("disk", 5.0))
        peso_val  = _calcular_peso(vcpus_val, ram_val, disk_val)

        nueva_vm = Vm(
            name=vm_data.get("id"),
            vcore=vcpus_val,
            ram=ram_val,
            disk=disk_val,
            state="DRAFT",
            slice_id=slice_id,
            image_id=vm_data.get("image_id"), 
            worker_id=vm_data.get("worker_id"),
            external_ip=ext_ip,
            internet_access=int_access,
            peso=peso_val,
            peso_actualizado=peso_val
        )
        db.add(nueva_vm)
        db.flush() # 🔥 Sincroniza temporalmente para obtener el ID de la VM

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

@router.get("/utils/available-ips", status_code=200)
def get_available_ips(db: Session = Depends(get_db)):
    # Solo devolvemos las que no están en uso
    ips = db.query(IpPool).filter(IpPool.is_used == 0).all()
    return [ip.ip_address for ip in ips]