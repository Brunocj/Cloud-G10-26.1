# app/services/slice_destroyer.py
"""
Lógica compartida de destrucción de un slice desplegado.

Usada por:
  · DELETE /api/v1/slices/{id}   (deploy_router — destrucción manual)
  · ttl_scheduler                 (auto-destrucción al vencer el TTL)
"""
import json
import logging
import os
import uuid
from datetime import datetime

from sqlalchemy.orm import Session

from app.models import IpPool, Slice, Vlan, Vm, Worker
from app.nats_producer import nats_producer

logger = logging.getLogger("SliceManager.Destroyer")


def _read_ssh_key(key_path: str) -> str:
    if not key_path:
        return ""
    for path in [key_path, f"/app/keys/{os.path.basename(key_path)}"]:
        if os.path.exists(path):
            try:
                return open(path).read()
            except Exception as exc:
                logger.error("[DESTROYER] Error leyendo clave '%s': %s", path, exc)
    return ""


def release_external_ips(db: Session, slice_id: int) -> None:
    vms = db.query(Vm).filter(Vm.slice_id == slice_id, Vm.external_ip.isnot(None)).all()
    ips = [vm.external_ip for vm in vms if vm.external_ip]
    if not ips:
        return
    for record in db.query(IpPool).filter(IpPool.ip_address.in_(ips)).all():
        record.is_used = 0
        record.vm_id = None


async def destroy_deployed_slice(db: Session, db_slice: Slice) -> bool:
    """
    Publica la orden de destrucción por NATS y marca el slice TERMINATED.
    Devuelve True si la orden fue publicada correctamente.
    El caller es responsable de la autorización y de decidir si el slice
    está realmente desplegado (no DRAFT/PENDING).
    """
    slice_id = db_slice.id

    db.query(Vlan).filter(Vlan.slice_id == slice_id).delete()

    s_json = db_slice.slice_json
    if isinstance(s_json, str):
        s_json = json.loads(s_json)
    if not s_json:
        s_json = {}

    # Re-inyectar claves SSH frescas desde BD. Se elige un worker DE LA ZONA del
    # slice y con ssh_key_path válido (no el primero de la tabla, que podría ser
    # de otra zona o sin llave → dejaría las VMs sin clave en el destroy).
    az = db_slice.availability_zone_id or 1
    key_worker = (
        db.query(Worker)
          .filter(Worker.availability_zones_id == az, Worker.ssh_key_path.isnot(None))
          .first()
        or db.query(Worker).filter(Worker.ssh_key_path.isnot(None)).first()
    )
    fresh_key = _read_ssh_key(key_worker.ssh_key_path) if key_worker else ""
    if not fresh_key:
        logger.error("[DESTROYER] ⚠️ No se obtuvo ssh_private_key para el slice %s (az=%s) — "
                     "el CP no podrá hacer SSH. Revisa ssh_key_path de los workers de esa zona.",
                     slice_id, az)

    deployed_vms   = s_json.get("deployed_vms", [])
    deployed_links = s_json.get("deployed_links", [])
    if fresh_key:
        for vm in deployed_vms:
            vm["ssh_private_key"] = fresh_key
        for link in deployed_links:
            for side in ("vm1", "vm2"):
                link[f"{side}_ssh_private_key"] = fresh_key

    payload = {
        "slice_id":             str(slice_id),
        "request_id":           f"req-destroy-{uuid.uuid4().hex[:8]}",
        "availability_zone_id": db_slice.availability_zone_id or 1,
        "vms":                  deployed_vms,
        "links":                deployed_links,
    }

    # Q-in-Q OpenStack: mapa SSH de computes para que Network limpie el patch
    # dot1q-tunnel (el S-VID lo lee Network de los deployed_links[].s_vlan_id).
    if (db_slice.availability_zone_id or 1) == 2:
        os_workers = db.query(Worker).filter(Worker.availability_zones_id == 2).all()
        payload["compute_ssh_map"] = {
            w.name: {"ip": w.ip, "port": getattr(w, "ssh_port", 22),
                     "user": getattr(w, "ssh_user", None),
                     "key": _read_ssh_key(getattr(w, "ssh_key_path", ""))}
            for w in os_workers if w.name
        }

    published = await nats_producer.publish_destroy(payload)
    if not published:
        return False

    db_slice.status = "TERMINATED"
    db_slice.date_destruction = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    db.query(Vm).filter(Vm.slice_id == slice_id).update({"state": "TERMINATED"})
    release_external_ips(db, slice_id)
    db.commit()
    return True
