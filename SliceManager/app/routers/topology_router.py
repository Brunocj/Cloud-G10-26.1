# app/routers/topology_router.py
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.schemas import DraftSaveRequest
from app.repositories.topology_repo import TopologyRepository

# Creamos un mini-FastAPI dedicado solo a las topologías
router = APIRouter(prefix="/slices", tags=["Slices / Topologies"])

@router.post("/api/v1/draft")
def create_draft(request: DraftSaveRequest, db: Session = Depends(get_db)):
    try:
        # Simulamos un usuario logueado (en el futuro esto vendrá del token JWT)
        usuario_actual_id = "user-123" 
        
        # Llamamos al repositorio para hacer el trabajo sucio
        topologia_creada = TopologyRepository.save_draft(
            db=db, 
            owner_id=usuario_actual_id, 
            payload=request.topology_json
        )
        
        return {
            "message": "Borrador híbrido guardado con éxito", 
            "topology_id": topologia_creada.id
        }
    except Exception as e:
        db.rollback() # Si algo falla, cancelamos todo para no dejar datos a medias
        raise HTTPException(status_code=500, detail=str(e))