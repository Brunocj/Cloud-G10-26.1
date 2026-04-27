from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, Enum, JSON
from sqlalchemy.orm import relationship
from app.database import Base
import enum

class SliceState(str, enum.Enum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    FAILED = "failed"
    TERMINATED = "terminated"

class User(Base):
    __tablename__ = "users"
    # Basado en la estructura de sdn_policy_sin_data.sql
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(100), unique=True, index=True, nullable=False)
    email = Column(String(100), unique=True, index=True, nullable=False)
    is_approved = Column(Boolean, default=False) # REQ-US-01: Flujo de aprobación
    
    # Relación: Un usuario puede tener muchos slices
    slices = relationship("Slice", back_populates="owner")

class Slice(Base):
    __tablename__ = "slices"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    owner_id = Column(Integer, ForeignKey("users.id"))
    state = Column(Enum(SliceState), default=SliceState.DRAFT)
    
    # REQ-US-08: Tiempo de Vida y Zona de Disponibilidad
    ttl_hours = Column(Integer, default=4)
    availability_zone = Column(String(50), nullable=False)
    
    # REQ-US-07: Persistencia de Borradores. Aquí guardamos el JSON del lienzo.
    topology_json = Column(JSON) 
    
    owner = relationship("User", back_populates="slices")