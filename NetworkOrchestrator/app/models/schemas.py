"""
Schemas para el Network Orchestrator.
Define los contratos de entrada (desde el Queue Manager) y salida.
Basado en conexiones (enlaces lógicos) para soportar cualquier topología.
"""

from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------
class ProvisioningStatus(str, Enum):
    SUCCESS = "success"
    ERROR   = "error"
    PARTIAL = "partial"

# ---------------------------------------------------------------------------
# Sub-modelos de entrada
# ---------------------------------------------------------------------------
class SecurityRule(BaseModel):
    allow_port: int
    protocol: str = "tcp"

class NetworkLink(BaseModel):
    """Representa un 'cable' (Capa 2) entre dos VMs en la infraestructura."""
    connection_id: str = Field(..., description="ID único del enlace en la BD")
    vlan_id:       int = Field(..., description="VLAN única asignada a este enlace (ej. 100)")
    
    # Datos del Extremo A (VM 1)
    vm1_id:              str = Field(..., description="ID de la VM 1")
    vm1_worker_ip:       str = Field(..., description="IP del servidor físico donde está la VM 1")
    vm1_tap:             str = Field(..., description="Interfaz TAP creada por Compute (ej: tap-vm1)")
    vm1_ssh_user:        str = Field(..., description="Usuario SSH del worker 1")
    vm1_ssh_private_key: str = Field(..., description="Llave PEM del worker 1")
    vm1_security_rules:  List[SecurityRule] = Field(default_factory=list)
    
    # Datos del Extremo B (VM 2)
    vm2_id:              str = Field(..., description="ID de la VM 2")
    vm2_worker_ip:       str = Field(..., description="IP del servidor físico donde está la VM 2")
    vm2_tap:             str = Field(..., description="Interfaz TAP creada por Compute (ej: tap-vm2)")
    vm2_ssh_user:        str = Field(..., description="Usuario SSH del worker 2")
    vm2_ssh_private_key: str = Field(..., description="Llave PEM del worker 2")
    vm2_security_rules:  List[SecurityRule] = Field(default_factory=list)

class DeployNetworkRequest(BaseModel):
    """Payload recibido en network.deploy"""
    slice_id:   str
    request_id: str
    links:      List[NetworkLink]

class DestroyNetworkRequest(BaseModel):
    """Payload recibido en network.destroy"""
    slice_id:   str
    request_id: str
    links:      Optional[List[NetworkLink]] = None  # 🔥 FIX AQUÍ
# ---------------------------------------------------------------------------
# Modelos de Salida (Respuesta al Queue Manager)
# ---------------------------------------------------------------------------
class LinkResult(BaseModel):
    connection_id: str
    error:         Optional[str] = None

class DeployNetworkResponse(BaseModel):
    slice_id:     str
    request_id:   str
    status:       ProvisioningStatus
    links_ok:     List[LinkResult] = Field(default_factory=list)
    links_failed: List[LinkResult] = Field(default_factory=list)

class DestroyNetworkResponse(BaseModel):
    slice_id:   str
    request_id: str
    status:     ProvisioningStatus
    error:      Optional[str] = None