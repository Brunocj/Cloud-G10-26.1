from sqlalchemy.orm import Session
from app.models import Slice

class SliceRepository:
    @staticmethod
    def save_draft(db: Session, request_name: str, payload: dict, owner_id: str):
        # Solo extraemos los enlaces para el JSON del slice
        enlaces = payload.get("edges", [])
        
        nuevo_slice = Slice(
            name=request_name, 
            status="DRAFT",
            creator_id=owner_id,
            slice_json={"edges": enlaces}
        )
        db.add(nuevo_slice)
        db.commit()
        db.refresh(nuevo_slice)
        
        return nuevo_slice