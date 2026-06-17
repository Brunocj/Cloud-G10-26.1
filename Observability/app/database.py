from sqlalchemy import create_engine, Column, Integer, Float, String, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from app.config import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME

DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}@{DB_HOST}/{DB_NAME}"

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Worker(Base):
    __tablename__ = "workers"

    id                    = Column(Integer, primary_key=True)
    cpu                   = Column(Integer)
    ram                   = Column(Integer)      # MB
    disk_gb               = Column(Float)
    availability_zones_id = Column(Integer)
    oc_cpu                = Column(Float, nullable=True)
    oc_ram                = Column(Float, nullable=True)
    oc_disco              = Column(Float, nullable=True)


class Slice(Base):
    __tablename__ = "slices"

    id            = Column(Integer, primary_key=True)
    status        = Column(String(45))
    date_deployed = Column(String(45), nullable=True)


class Vm(Base):
    __tablename__ = "vms"

    id        = Column(Integer, primary_key=True)
    name      = Column(String(45))
    worker_id = Column(Integer, nullable=True)
    slice_id  = Column(Integer, ForeignKey("slices.id"), nullable=True)
    state     = Column(String(45))
    vcore     = Column(Integer)
    ram       = Column(Float)            # MB
    disk      = Column(Float)            # GB


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
