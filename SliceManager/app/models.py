# coding: utf-8
from sqlalchemy import Column, Float, ForeignKey, Integer, JSON, String
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
    ram = Column(Float(asdecimal=True))
    cpu = Column(Integer)
    date_created = Column(String(45))
    availability_zones_id = Column(ForeignKey('availability_zones.id'), index=True)

    availability_zones = relationship('AvailabilityZone')


class Image(Base):
    __tablename__ = 'images'

    id = Column(Integer, primary_key=True)
    user_id = Column(ForeignKey('users.id'), index=True)
    name = Column(String(45))
    date_uploaded = Column(String(100))
    is_general = Column(TINYINT)
    path = Column(String(150))

    user = relationship('User')


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
    creator_id = Column(ForeignKey('users.id'), index=True)
    slice_json = Column(JSON)

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
    external_ip = Column(String(45))
    slice_id = Column(ForeignKey('slices.id'), index=True)
    image_id = Column(ForeignKey('images.id'), index=True)
    vnc_port = Column(Integer)
    worker_id = Column(ForeignKey('workers.id'), index=True)

    image = relationship('Image')
    slice = relationship('Slice')
    worker = relationship('Worker')
