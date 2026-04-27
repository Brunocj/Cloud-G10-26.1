from pydantic import BaseModel
from typing import Optional, Dict, Any

# Lo que el Frontend nos envía al presionar "Solicitar Despliegue" (REQ-US-08)
class DeployRequest(BaseModel):
    availability_zone: str
    ttl_hours: int
    motivo: str # El TDR exige este campo de texto
    
# Lo que el Frontend nos envía al guardar un borrador (REQ-US-07)
class DraftSaveRequest(BaseModel):
    name: str
    topology_json: Dict[str, Any] # El JSON libre que viene del lienzo