from pydantic import BaseModel
from typing import Optional, Dict, Any

# ── Schemas de Imagen ────────────────────────────────────────────────────────

class ImageResponse(BaseModel):
    """Esquema de respuesta del catálogo híbrido de imágenes (Linux Cluster + OpenStack)."""
    id: int
    name: str
    availability_zone_id: Optional[int] = None
    az_name: Optional[str] = None          # "Linux Cluster" | "OpenStack" | None
    path: Optional[str] = None
    is_general: Optional[int] = None
    date_uploaded: Optional[str] = None
    in_use: bool = False
    active_vm_count: int = 0
    cloud_init_support: int = 0
    default_username: Optional[str] = None
    default_password: Optional[str] = None

    class Config:
        from_attributes = True


# ── Schemas de Despliegue ────────────────────────────────────────────────────

# Lo que el Frontend nos envía al presionar "Solicitar Despliegue" (REQ-US-08)
class DeployRequest(BaseModel):
    availability_zone_id: int
    ttl_hours: int
    motivo: str

# Lo que el Frontend nos envía al guardar un borrador (REQ-US-07)
class DraftSaveRequest(BaseModel):
    name: str
    slice_json: Dict[str, Any] # El JSON libre que viene del lienzo