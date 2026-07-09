# app/routers/infra_router.py
"""
Gestión de infraestructura física (REQ-SA-02 / REQ-SA-03) — solo superAdmin.

  Workers (nodos de cómputo):
    GET    /api/v1/infra/workers
    POST   /api/v1/infra/workers
    PUT    /api/v1/infra/workers/{id}
    DELETE /api/v1/infra/workers/{id}       (bloqueado si tiene VMs vivas)

  Zonas de disponibilidad:
    GET    /api/v1/infra/zones
    POST   /api/v1/infra/zones
    DELETE /api/v1/infra/zones/{id}         (bloqueado si tiene workers)
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import CurrentUser, require_roles
from app.database import get_db
from app.models import AvailabilityZone, Vm, Worker
from app.services.audit import audit

logger = logging.getLogger("SliceManager.Infra")

router = APIRouter(prefix="/api/v1/infra", tags=["Infrastructure"])

_VM_ALIVE = ("DRAFT", "PROVISIONING", "ACTIVE", "PENDING_APPROVAL")


# ── Schemas ────────────────────────────────────────────────────────────────────

class WorkerPayload(BaseModel):
    name: str
    ip: str
    ssh_port: Optional[int] = 22
    ssh_user: Optional[str] = None
    ssh_key_path: Optional[str] = None
    cpu: Optional[int] = None            # vCPUs nominales
    ram: Optional[float] = None          # MB nominales
    disk_gb: Optional[float] = None
    availability_zones_id: Optional[int] = None


class WorkerUpdatePayload(BaseModel):
    name: Optional[str] = None
    ip: Optional[str] = None
    ssh_port: Optional[int] = None
    ssh_user: Optional[str] = None
    ssh_key_path: Optional[str] = None
    cpu: Optional[int] = None
    ram: Optional[float] = None
    disk_gb: Optional[float] = None
    availability_zones_id: Optional[int] = None


class ZonePayload(BaseModel):
    name: str


class TestConnectionPayload(BaseModel):
    ip: str
    ssh_port: Optional[int] = 22
    ssh_user: Optional[str] = None
    ssh_key_path: Optional[str] = None


def _serialize_worker(db: Session, w: Worker) -> dict:
    active_vms = db.query(Vm).filter(Vm.worker_id == w.id, Vm.state.in_(_VM_ALIVE)).count()
    return {
        "id": w.id, "name": w.name, "ip": w.ip,
        "ssh_port": w.ssh_port, "ssh_user": w.ssh_user, "ssh_key_path": w.ssh_key_path,
        "cpu": w.cpu, "ram": float(w.ram) if w.ram else None, "disk_gb": w.disk_gb,
        "oc_cpu": w.oc_cpu, "oc_ram": w.oc_ram, "oc_disco": w.oc_disco,
        "availability_zones_id": w.availability_zones_id,
        "zone_name": w.availability_zones.name if w.availability_zones else None,
        "active_vms": active_vms,
        "date_created": w.date_created,
    }


# ── Workers ────────────────────────────────────────────────────────────────────

@router.get("/workers")
def list_workers(
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(require_roles("superAdmin")),
):
    return [_serialize_worker(db, w) for w in db.query(Worker).order_by(Worker.id).all()]


@router.post("/workers", status_code=201)
def create_worker(
    payload: WorkerPayload,
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(require_roles("superAdmin")),
):
    if payload.availability_zones_id is not None:
        if not db.query(AvailabilityZone).filter(AvailabilityZone.id == payload.availability_zones_id).first():
            raise HTTPException(status_code=400, detail="Zona de disponibilidad inexistente")
    if db.query(Worker).filter(Worker.name == payload.name).first():
        raise HTTPException(status_code=409, detail=f"Ya existe un worker llamado '{payload.name}'")

    w = Worker(
        **payload.model_dump(),
        date_created=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
    )
    db.add(w)
    db.commit()
    db.refresh(w)
    logger.info("[INFRA] Worker creado: %s (%s) por %s…", w.name, w.ip, user.user_id[:8])
    audit(user.user_id, user.role, "Infra", "worker_created",
          f"Worker '{w.name}' ({w.ip}) matriculado en zona {w.availability_zones_id}.")
    return _serialize_worker(db, w)


@router.put("/workers/{worker_id}")
def update_worker(
    worker_id: int,
    payload:   WorkerUpdatePayload,
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(require_roles("superAdmin")),
):
    w = db.query(Worker).filter(Worker.id == worker_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Worker no encontrado")

    changes = payload.model_dump(exclude_none=True)
    if "availability_zones_id" in changes:
        if not db.query(AvailabilityZone).filter(AvailabilityZone.id == changes["availability_zones_id"]).first():
            raise HTTPException(status_code=400, detail="Zona de disponibilidad inexistente")
        # Cambiar de zona con VMs vivas rompería el placement de esas VMs
        if changes["availability_zones_id"] != w.availability_zones_id:
            alive = db.query(Vm).filter(Vm.worker_id == worker_id, Vm.state.in_(_VM_ALIVE)).count()
            if alive > 0:
                raise HTTPException(
                    status_code=409,
                    detail=f"El worker tiene {alive} VM(s) vivas; destrúyelas antes de cambiarlo de zona.",
                )

    for key, value in changes.items():
        setattr(w, key, value)
    db.commit()
    db.refresh(w)
    audit(user.user_id, user.role, "Infra", "worker_updated",
          f"Worker '{w.name}' actualizado: {', '.join(changes.keys())}.")
    return _serialize_worker(db, w)


@router.delete("/workers/{worker_id}")
def delete_worker(
    worker_id: int,
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(require_roles("superAdmin")),
):
    w = db.query(Worker).filter(Worker.id == worker_id).first()
    if not w:
        raise HTTPException(status_code=404, detail="Worker no encontrado")

    alive = db.query(Vm).filter(Vm.worker_id == worker_id, Vm.state.in_(_VM_ALIVE)).count()
    if alive > 0:
        raise HTTPException(
            status_code=409,
            detail=f"El worker tiene {alive} VM(s) vivas. Destruye sus slices antes de desmatricularlo.",
        )

    # Desvincular VMs históricas (TERMINATED/FAILED) para no romper la FK
    db.query(Vm).filter(Vm.worker_id == worker_id).update({"worker_id": None})
    name = w.name
    db.delete(w)
    db.commit()
    logger.warning("[INFRA] Worker '%s' desmatriculado por %s…", name, user.user_id[:8])
    audit(user.user_id, user.role, "Infra", "worker_deleted",
          f"Worker '{name}' desmatriculado de la plataforma.", level="WARNING")
    return {"message": f"Worker '{name}' eliminado"}


@router.post("/workers/test-connection")
def test_worker_connection(
    payload: TestConnectionPayload,
    user: CurrentUser = Depends(require_roles("superAdmin")),
):
    """
    Wizard de validación del REQ-SA-02: intenta autenticarse por SSH al host y
    lee sus recursos reales (nproc, RAM, disco) para verificar que el registro
    es correcto ANTES de guardarlo.
    """
    import os
    import paramiko

    if not payload.ssh_user or not payload.ssh_key_path:
        raise HTTPException(status_code=400, detail="Se requieren usuario SSH y ruta de la llave para probar.")

    # Resolver la llave igual que el resto de la plataforma (path BD o /app/keys)
    key_path = None
    for candidate in [payload.ssh_key_path, f"/app/keys/{os.path.basename(payload.ssh_key_path)}"]:
        if os.path.exists(candidate):
            key_path = candidate
            break
    if not key_path:
        raise HTTPException(status_code=400, detail=f"La llave SSH no existe en el contenedor: {payload.ssh_key_path}")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            payload.ip, port=payload.ssh_port or 22,
            username=payload.ssh_user, key_filename=key_path, timeout=8,
        )
        def run(cmd: str) -> str:
            _, stdout, _ = client.exec_command(cmd, timeout=8)
            return stdout.read().decode().strip()

        hostname = run("hostname")
        nproc    = run("nproc")
        ram_mb   = run("free -m | awk '/^Mem:/{print $2}'")
        disk_gb  = run("df -BG / | awk 'NR==2{gsub(\"G\",\"\",$2); print $2}'")
        kvm      = run("test -e /dev/kvm && echo yes || echo no")

        audit(user.user_id, user.role, "Infra", "worker_tested",
              f"Test de conexión OK a {payload.ip}: host={hostname} cpu={nproc} ram={ram_mb}MB kvm={kvm}.")
        return {
            "ok": True,
            "hostname": hostname,
            "cpu":      int(nproc) if nproc.isdigit() else None,
            "ram_mb":   int(ram_mb) if ram_mb.isdigit() else None,
            "disk_gb":  int(disk_gb) if disk_gb.isdigit() else None,
            "kvm":      kvm == "yes",
            "message":  f"Conexión exitosa a {hostname} ({payload.ip})",
        }
    except Exception as exc:
        logger.warning("[INFRA] Test de conexión a %s falló: %s", payload.ip, exc)
        return {"ok": False, "message": f"Fallo de conexión: {exc}"}
    finally:
        client.close()


# ── Zonas de disponibilidad ────────────────────────────────────────────────────

@router.get("/zones")
def list_zones(
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(require_roles("superAdmin")),
):
    zones = db.query(AvailabilityZone).order_by(AvailabilityZone.id).all()
    return [{
        "id": z.id,
        "name": z.name,
        "worker_count": db.query(Worker).filter(Worker.availability_zones_id == z.id).count(),
    } for z in zones]


@router.post("/zones", status_code=201)
def create_zone(
    payload: ZonePayload,
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(require_roles("superAdmin")),
):
    if db.query(AvailabilityZone).filter(AvailabilityZone.name == payload.name).first():
        raise HTTPException(status_code=409, detail="Ya existe una zona con ese nombre")
    z = AvailabilityZone(name=payload.name)
    db.add(z)
    db.commit()
    db.refresh(z)
    audit(user.user_id, user.role, "Infra", "zone_created", f"Zona '{z.name}' creada.")
    return {"id": z.id, "name": z.name, "worker_count": 0}


@router.delete("/zones/{zone_id}")
def delete_zone(
    zone_id: int,
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(require_roles("superAdmin")),
):
    z = db.query(AvailabilityZone).filter(AvailabilityZone.id == zone_id).first()
    if not z:
        raise HTTPException(status_code=404, detail="Zona no encontrada")
    workers = db.query(Worker).filter(Worker.availability_zones_id == zone_id).count()
    if workers > 0:
        raise HTTPException(status_code=409, detail=f"La zona tiene {workers} worker(s) asignados.")
    name = z.name
    db.delete(z)
    db.commit()
    audit(user.user_id, user.role, "Infra", "zone_deleted", f"Zona '{name}' eliminada.", level="WARNING")
    return {"message": f"Zona '{name}' eliminada"}
