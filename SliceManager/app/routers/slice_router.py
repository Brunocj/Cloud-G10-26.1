# app/routers/slice_router.py
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, require_roles
from app.database import get_db
from app.models import Image, IpPool, Slice, Vm, Worker
from app.repositories.slice_repo import SliceRepository
from app.schemas import DraftSaveRequest

logger = logging.getLogger("SliceManager.slices")

router = APIRouter(prefix="/api/v1/slices", tags=["Slices / Topologies"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _serialize_slice(t: Slice) -> dict:
    """Serializa un objeto Slice a dict para la respuesta de la API."""
    s_json = t.slice_json if t.slice_json else {}
    if isinstance(s_json, str):
        s_json = json.loads(s_json)

    nodes = s_json.get("nodes", [])
    edges = s_json.get("edges", [])

    # Fusionar IP y VNC Port en los nodos del frontend
    deployed_vms = s_json.get("deployed_vms", [])
    if deployed_vms:
        for node in nodes:
            for d_vm in deployed_vms:
                if node.get("id") == d_vm.get("vm_id"):
                    node["worker_ip"]   = d_vm.get("worker_ip")
                    node["worker_port"] = d_vm.get("worker_port")   # puerto SSH al gateway
                    node["vnc_port"]    = d_vm.get("vnc_port")

    return {
        "id":        t.id,
        "name":      t.name if t.name else f"Slice {t.id}",
        "status":    t.status,
        "owner_id":  t.creator_id,
        "nodeCount": len(nodes),
        "edgeCount": len(edges),
        "vcpus":     sum(int(n.get("vcores", 0)) for n in nodes),
        "ramLabel":  f"{sum(float(n.get('ram', 0)) for n in nodes)} MB",
        "nodes":     nodes,
        "edges":     edges,
    }


def _assert_owner_or_admin(db_slice: Slice, user: CurrentUser) -> None:
    """
    Lanza 403 si el usuario no es el dueño del slice ni tiene rol admin/superAdmin.
    Regla de negocio: un 'usuario' o 'jefeProyecto' solo puede operar sus propios slices.
    """
    if not user.is_owner_or_above(db_slice.creator_id, min_role="admin"):
        raise HTTPException(
            status_code=403,
            detail="No tienes permiso para operar sobre el slice de otro usuario.",
        )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/", status_code=200)
def list_slices(
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Lista slices.
    - admin / superAdmin → ven TODOS los slices de la plataforma.
    - jefeProyecto / usuario → solo ven los suyos.
    """
    query = db.query(Slice)

    if not user.can_manage_all():
        # Filtrar únicamente los slices del usuario autenticado
        query = query.filter(Slice.creator_id == user.user_id)

    slices = query.order_by(Slice.id.desc()).all()
    return [_serialize_slice(t) for t in slices]


@router.post("/draft")
def create_draft(
    request: DraftSaveRequest,
    db:      Session     = Depends(get_db),
    user:    CurrentUser = Depends(get_current_user),
):
    """Crea un borrador de slice. Cualquier rol autenticado puede crear slices."""
    try:
        slice_creado = SliceRepository.save_draft(
            db=db,
            owner_id=user.user_id,          # ← ID real del JWT
            payload=request.slice_json,
            request_name=request.name,
        )

        workers_db  = db.query(Worker).all()
        num_workers = len(workers_db)
        nodes_list  = request.slice_json.get("nodes", [])

        for index, vm_data in enumerate(nodes_list):
            asignado = workers_db[index % num_workers] if num_workers > 0 else None

            ext_ip     = vm_data.get("external_ip", None)
            int_access = 1 if ext_ip else int(vm_data.get("internet_access", 0))

            img_id = vm_data.get("image_id")
            if img_id is not None and int(img_id) < 0:
                img_id = None

            nueva_vm = Vm(
                name=vm_data.get("id"),
                vcore=int(vm_data.get("vcores", 1)),
                ram=float(vm_data.get("ram", 512.0)),
                disk=float(vm_data.get("disk", 5.0)),
                state="DRAFT",
                slice_id=slice_creado.id,
                image_id=img_id,
                worker_id=asignado.id if asignado else None,
                external_ip=ext_ip,
                internet_access=int_access,
            )

            vm_data["worker"]    = asignado.name if asignado else "Unassigned"
            vm_data["worker_id"] = asignado.id if asignado else None

            db.add(nueva_vm)
            db.flush()

            if ext_ip:
                ip_record = db.query(IpPool).filter(
                    IpPool.ip_address == ext_ip, IpPool.is_used == 0
                ).first()
                if ip_record:
                    ip_record.is_used = 1
                    ip_record.vm_id   = nueva_vm.id

        slice_creado.slice_json = {
            "nodes": nodes_list,
            "edges": request.slice_json.get("edges", []),
        }
        db.commit()

        logger.info(
            "Borrador creado: slice_id=%s owner=%s…",
            slice_creado.id, user.user_id[:8],
        )
        return {"message": "Borrador guardado con éxito", "slice_id": slice_creado.id}

    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/{slice_id}/draft", status_code=200)
def update_draft(
    slice_id: int,
    request:  DraftSaveRequest,
    db:       Session     = Depends(get_db),
    user:     CurrentUser = Depends(get_current_user),
):
    """
    Actualiza un borrador.
    - Solo el dueño (o admin/superAdmin) puede editarlo.
    """
    db_slice = db.query(Slice).filter(Slice.id == slice_id).first()
    if not db_slice:
        raise HTTPException(status_code=404, detail="Slice no encontrado")

    if db_slice.status != "DRAFT":
        raise HTTPException(status_code=400, detail="Solo se pueden editar borradores")

    # ── Autorización de negocio ────────────────────────────────────
    _assert_owner_or_admin(db_slice, user)

    db_slice.slice_json = {"edges": request.slice_json.get("edges", [])}

    # Liberar IPs antes de destruir VMs viejas
    vms_antiguas = db.query(Vm).filter(Vm.slice_id == slice_id).all()
    for v in vms_antiguas:
        if v.external_ip:
            ip_record = db.query(IpPool).filter(
                IpPool.ip_address == v.external_ip
            ).first()
            if ip_record:
                ip_record.is_used = 0
                ip_record.vm_id   = None

    db.query(Vm).filter(Vm.slice_id == slice_id).delete()

    for vm_data in request.slice_json.get("nodes", []):
        ext_ip     = vm_data.get("external_ip", None)
        int_access = 1 if ext_ip else int(vm_data.get("internet_access", 0))

        img_id = vm_data.get("image_id")
        if img_id is not None and int(img_id) < 0:
            img_id = None

        nueva_vm = Vm(
            name=vm_data.get("id"),
            vcore=int(vm_data.get("vcores", 1)),
            ram=float(vm_data.get("ram", 512.0)),
            disk=float(vm_data.get("disk", 5.0)),
            state="DRAFT",
            slice_id=slice_id,
            image_id=img_id,
            worker_id=vm_data.get("worker_id"),
            external_ip=ext_ip,
            internet_access=int_access,
        )
        db.add(nueva_vm)
        db.flush()

    db.commit()
    logger.info(
        "Borrador actualizado: slice_id=%s por user=%s…",
        slice_id, user.user_id[:8],
    )
    return {"message": "Borrador actualizado con éxito"}


# ── Utils (sin restricción de rol — cualquier usuario autenticado) ─────────────



@router.get("/utils/workers", status_code=200)
def get_available_workers(
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),   # noqa: autenticado
):
    workers = db.query(Worker).all()
    if not workers:
        return ["server1"]
    return [w.name for w in workers]


@router.get("/utils/available-ips", status_code=200)
def get_available_ips(
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),   # noqa: autenticado
):
    ips = db.query(IpPool).filter(IpPool.is_used == 0).all()
    return [ip.ip_address for ip in ips]