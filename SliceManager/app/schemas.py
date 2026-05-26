from pydantic import BaseModel
from typing import Optional, Dict, Any

# Lo que el Frontend nos envía al presionar "Solicitar Despliegue" (REQ-US-08)
class DeployRequest(BaseModel):
    availability_zone_id: int
    ttl_hours: int
    motivo: str
    
# Lo que el Frontend nos envía al guardar un borrador (REQ-US-07)
class DraftSaveRequest(BaseModel):
    name: str
    slice_json: Dict[str, Any] # El JSON libre que viene del lienzo