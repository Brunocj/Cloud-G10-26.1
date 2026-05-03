from sqlalchemy.orm import Session
from app.models import Slice, Vm

class SliceRepository:
    @staticmethod
    def save_draft(db: Session, request_name: str, payload: dict, owner_id: str):
        nodos_vms = payload.get("nodes", [])
        enlaces = payload.get("edges", [])
        
        nuevo_slice = Slice(
            name=request_name, # 🔥 FIX: Ahora sí lo guardamos en la columna real
            status="DRAFT",
            creator_id=owner_id,
            slice_json={"edges": enlaces}
        )
        db.add(nuevo_slice)
        db.flush()
        
        # 2. Guardamos las VMs en la tabla relacional
        for vm_data in nodos_vms:
            if vm_data.get("type") == "vm":
                nueva_vm = Vm(
                    name=vm_data.get("id"),
                    vcore=str(vm_data.get("vcpus", 1)),
                    ram=str(vm_data.get("ram_mb", 512)),
                    state="DRAFT",
                    slice_id=nuevo_slice.id,
                    # Soporte para la nueva IP Externa. Si no viene, será NULL
                    external_ip=vm_data.get("external_ip", None) 
                )
                db.add(nueva_vm)
                
        db.commit()
        db.refresh(nuevo_slice)
        return nuevo_slice