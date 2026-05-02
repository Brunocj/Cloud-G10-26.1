# app/models.py
from sqlalchemy import Column, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()
metadata = Base.metadata


class AvailabilityZone(Base):
    __tablename__ = 'availability_zones'

    id = Column(Integer, primary_key=True)
    _name = Column(' name', String(100))


class Career(Base):
    __tablename__ = 'careers'

    id = Column(Integer, primary_key=True)
    name = Column(String(100), unique=True)


class Image(Base):
    __tablename__ = 'images'

    id = Column(Integer, primary_key=True)
    name = Column(String(45))
    date_uploaded = Column(String(100))
    is_general = Column(TINYINT)
    img_path = Column(String(150))


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
    username = Column(String(100), unique=True)
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
    ram = Column(Float(asdecimal=True))
    cpu = Column(Integer)
    date_created = Column(String(45))
    availability_zones_id = Column(ForeignKey('availability_zones.id'), index=True)

    availability_zones = relationship('AvailabilityZone')


class Topology(Base):
    __tablename__ = 'topologies'

    id = Column(Integer, primary_key=True)
    status = Column(String(45))
    template_type = Column(TINYINT)
    availability_zone_id = Column(ForeignKey('availability_zones.id'), index=True)
    TTL = Column(Float(asdecimal=True))
    date_destruction = Column(String(45))
    date_deployed = Column(String(45))
    project_id = Column(ForeignKey('projects.id'), index=True)
    creator_id = Column(ForeignKey('users.id'), index=True)

    availability_zone = relationship('AvailabilityZone')
    creator = relationship('User')
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


class Vm(Base):
    __tablename__ = 'vms'

    id = Column(Integer, primary_key=True)
    name = Column(String(45))
    vcore = Column(String(45))
    ram = Column(String(45))
    state = Column(String(45))
    date_uploaded = Column(String(100))
    topologies_id = Column(ForeignKey('topologies.id'), index=True)
    image_id = Column(ForeignKey('images.id'), index=True)

    image = relationship('Image')
    topologies = relationship('Topology')
