# coding: utf-8
from sqlalchemy import Boolean, Column, Float, ForeignKey, Integer, JSON, String
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()
metadata = Base.metadata


class AvailabilityZone(Base):
    __tablename__ = 'availability_zones'

    id = Column(Integer, primary_key=True)
    name = Column(String(100))


class Career(Base):
    __tablename__ = 'careers'

    id = Column(Integer, primary_key=True)
    name = Column(String(100))


class Project(Base):
    __tablename__ = 'projects'

    id = Column(Integer, primary_key=True)
    name = Column(String(100))
    description = Column(String(45))
    date_creation = Column(String(45))


class Role(Base):
    __tablename__ = 'roles'

    id = Column(Integer, primary_key=True)
    role_name = Column(String(50))


class User(Base):
    __tablename__ = 'users'

    id = Column(String(100), primary_key=True)
    username = Column(String(100))
    fullname = Column(String(100))
    lastname = Column(String(100))
    email = Column(String(100))
    career_id = Column(ForeignKey('careers.id', ondelete='SET NULL', onupdate='CASCADE'), index=True)
    pucp_code = Column(String(45))
    state = Column(String(45))
    date_creation = Column(String(45))
    role_id = Column(ForeignKey('roles.id'), index=True)

    career = relationship('Career')
    role = relationship('Role')


class Worker(Base):
    __tablename__ = 'workers'

    id = Column(Integer, primary_key=True)
    name = Column(String(45))
    ip = Column(String(45))
    ssh_port = Column(Integer, nullable=True)
    ssh_user = Column(String(50), nullable=True)
    ssh_key_path = Column(String(200), nullable=True)
    ram = Column(Float(asdecimal=True))
    cpu = Column(Integer)
    disk_gb = Column(Float, nullable=True)
    oc_cpu   = Column(Float, nullable=True)  # Calculado por Observabilidad: C_nominal_cpu / (μ_cpu + z_cpu·σ_cpu)
    oc_ram   = Column(Float, nullable=True)  # Calculado por Observabilidad: C_nominal_ram / (μ_ram + z_ram·σ_ram)
    oc_disco = Column(Float, nullable=True)  # Siempre 1.0 — sin overcommit
    date_created = Column(String(45))
    availability_zones_id = Column(ForeignKey('availability_zones.id'), index=True)

    availability_zones = relationship('AvailabilityZone')


class Image(Base):
    __tablename__ = 'images'

    id = Column(Integer, primary_key=True)
    # user_id almacena el UUID de Keycloak (sub del JWT)
    user_id = Column(String(100), index=True)
    name = Column(String(45))
    date_uploaded = Column(String(100))
    is_general = Column(TINYINT)
    path = Column(String(150))
    # Zona de disponibilidad a la que pertenece esta imagen (un registro por AZ)
    availability_zone_id = Column(Integer, ForeignKey('availability_zones.id'), index=True, nullable=True)
    # Indica si la imagen soporta cloud-init (permite inyectar credenciales/red dinámicamente).
    # Si es False (ej. CirrOS), las credenciales son fijas y deben registrarse aquí.
    cloud_init_support = Column(TINYINT, default=0)
    default_username = Column(String(50), nullable=True)
    default_password = Column(String(100), nullable=True)

    availability_zone = relationship('AvailabilityZone')


class Slice(Base):
    __tablename__ = 'slices'

    id = Column(Integer, primary_key=True)
    name = Column(String(100))
    status = Column(String(45))
    template_type = Column(TINYINT)
    availability_zone_id = Column(ForeignKey('availability_zones.id'), index=True)
    TTL = Column(Float(asdecimal=True))
    date_destruction = Column(String(45))
    date_deployed = Column(String(45))
    project_id = Column(ForeignKey('projects.id'), index=True)
    # creator_id almacena el UUID de Keycloak (sub del JWT).
    # Ya NO es FK hacia users — Keycloak es la fuente de verdad de identidad.
    creator_id = Column(String(100), index=True)
    slice_json = Column(MutableDict.as_mutable(JSON))

    availability_zone = relationship('AvailabilityZone')
    project = relationship('Project')


class UserProject(Base):
    __tablename__ = 'user_projects'

    id = Column(Integer, primary_key=True)
    user_id = Column(ForeignKey('users.id', ondelete='CASCADE', onupdate='CASCADE'), index=True)
    project_id = Column(ForeignKey('projects.id', ondelete='CASCADE', onupdate='CASCADE'), index=True)
    project_role_id = Column(ForeignKey('roles.id'), index=True)
    date_join = Column(String(45))

    project = relationship('Project')
    project_role = relationship('Role')
    user = relationship('User')


class Vlan(Base):
    __tablename__ = 'vlans'

    id = Column(Integer, primary_key=True)
    slice_id = Column(ForeignKey('slices.id'), index=True)
    type = Column(String(45))

    slice = relationship('Slice')


class Vm(Base):
    __tablename__ = 'vms'

    id = Column(Integer, primary_key=True)
    name = Column(String(45))
    vcore = Column(Integer)
    ram = Column(Float(asdecimal=True))
    disk = Column(Float(asdecimal=True))
    state = Column(String(45))
    external_ip = Column(String(45))        # Reutilizado también como IP flotante de OpenStack
    internet_access = Column(TINYINT, default=0)
    slice_id = Column(ForeignKey('slices.id'), index=True)
    image_id = Column(ForeignKey('images.id'), index=True)
    vnc_port = Column(Integer)
    worker_id = Column(ForeignKey('workers.id'), index=True)
    # Campos OpenStack Nova
    provider_instance_id = Column(String(100), nullable=True)  # UUID de la instancia en Nova
    vnc_url = Column(String(500), nullable=True)               # URL web NoVNC de OpenStack

    image = relationship('Image')
    slice = relationship('Slice')
    worker = relationship('Worker')

class AuditLog(Base):
    """
    Bitácora de eventos (REQ-JP-09 / REQ-AD-08).
    Registra el ciclo de vida transaccional: solicitudes, aprobaciones,
    despliegues, destrucciones (manuales, TTL y kill switch) y acciones
    administrativas (usuarios, roles, proyectos, infraestructura).
    """
    __tablename__ = 'audit_logs'

    id = Column(Integer, primary_key=True)
    timestamp = Column(String(45), index=True)          # UTC "YYYY-MM-DD HH:MM:SS"
    level = Column(String(10), default="INFO")          # INFO | WARNING | ERROR
    actor_id = Column(String(100), index=True)          # UUID Keycloak o "system"
    actor_role = Column(String(45))
    module = Column(String(60))                         # SliceManager, TTL, KillSwitch, IAM…
    action = Column(String(60), index=True)             # deploy_requested, approved, destroyed…
    detail = Column(String(500))
    slice_id = Column(Integer, nullable=True, index=True)
    project_id = Column(Integer, nullable=True, index=True)


class IpPool(Base):
    __tablename__ = 'ip_pool'

    id = Column(Integer, primary_key=True)
    ip_address = Column(String(45), unique=True)
    is_used = Column(TINYINT, default=0)
    vm_id = Column(ForeignKey('vms.id', ondelete='SET NULL'), index=True)
    # Zona de disponibilidad a la que pertenece esta IP (1=Linux Cluster, 2=OpenStack)
    availability_zone_id = Column(Integer, ForeignKey('availability_zones.id'), index=True, nullable=True)

    availability_zone = relationship('AvailabilityZone')

    vm = relationship('Vm')
