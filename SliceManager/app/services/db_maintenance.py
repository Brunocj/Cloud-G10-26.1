# app/services/db_maintenance.py
"""
Auditoría y limpieza de inconsistencias en la BD de slices/VMs.

No toca infraestructura física (SSH/NATS) — solo corrige filas de MySQL que
quedaron sueltas por bugs ya corregidos, ediciones manuales de la BD, o
saltos de estado que ocurrieron sin pasar por el flujo normal (destroy,
gc_scheduler, stuck_ops_scheduler). Pensado para correrse a demanda desde
`GET/POST /api/v1/maintenance/*` (admin/superAdmin), con `dry_run=True` por
defecto en la limpieza.

Categorías corregibles:
  orphan_vms       VM cuyo slice_id ya no existe en `slices`.
  orphan_vlans     VLAN cuyo slice_id ya no existe en `slices`.
  stale_ip_pool    IP marcada `is_used=1` sin una VM viva detrás (huérfana o
                   de una VM ya TERMINATED/FAILED que debió liberarla).
  zombie_vms       VM en estado vivo (DRAFT/PROVISIONING/ACTIVE/PENDING_APPROVAL)
                   cuyo slice ya está en un estado terminal — nunca se le
                   propagó el cierre, así que retiene su vnc_port para
                   siempre (ver el filtro `vnc_por_worker` de placement_worker.py).
  terminated_vms   VM en estado TERMINATED — historial de instancias ya
                   destruidas. Ninguna vista de la plataforma las lee (el
                   historial de un slice se sirve desde `slice.slice_json`,
                   no de la tabla `vms`), así que purgarlas es solo limpieza
                   de espacio; no se tocan las VMs FAILED (quedan como
                   evidencia de despliegues fallidos).
  terminated_slices Slice en estado TERMINATED — el despliegue completo ya
                   fue destruido y confirmado. Se borran también sus VMs
                   (liberando cualquier IP que hubiera quedado) y cualquier
                   VLAN residual, porque `vms.slice_id`/`vlans.slice_id` son
                   FK hacia `slices.id` y no se puede borrar el padre sin
                   soltar antes a los hijos. No se tocan los slices FAILED
                   ni REJECTED (quedan como evidencia para debug).
  empty_drafts     Slice DRAFT sin ninguna VM asociada.

Solo informativa (nunca se toca desde `clean`):
  stuck_operations TERMINATING/PROVISIONING con una operación pendiente más
                   vieja que STUCK_OP_TIMEOUT_MINUTES. Ya los procesa
                   `stuck_ops_scheduler`, que sabe revertir con seguridad sin
                   liberar VLANs/IPs de infraestructura que podría seguir viva.
"""
import json
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.models import IpPool, Slice, Vlan, Vm
from app.services.stuck_ops_scheduler import STUCK_OP_TIMEOUT_MINUTES

logger = logging.getLogger("SliceManager.DBMaintenance")

_ALIVE_VM_STATES = ("DRAFT", "PROVISIONING", "ACTIVE", "PENDING_APPROVAL")
_TERMINAL_VM_STATE = {"TERMINATED": "TERMINATED", "FAILED": "FAILED", "REJECTED": "FAILED"}
_DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S")

CATEGORIES = (
    "orphan_vms", "orphan_vlans", "stale_ip_pool", "zombie_vms",
    "terminated_vms", "terminated_slices", "empty_drafts",
)


def _parse_date(raw):
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except (ValueError, TypeError):
            continue
    return None


def _slice_json(sl: Slice) -> dict:
    sj = sl.slice_json or {}
    if isinstance(sj, str):
        try:
            sj = json.loads(sj)
        except (ValueError, TypeError):
            return {}
    return sj or {}


def _existing_slice_ids(db: Session) -> list:
    return [row[0] for row in db.query(Slice.id).all()]


def _find_orphan_vms(db: Session, slice_ids: list) -> list:
    return db.query(Vm).filter(Vm.slice_id.isnot(None), ~Vm.slice_id.in_(slice_ids)).all()


def _find_orphan_vlans(db: Session, slice_ids: list) -> list:
    return db.query(Vlan).filter(Vlan.slice_id.isnot(None), ~Vlan.slice_id.in_(slice_ids)).all()


def _find_stale_ip_pool(db: Session) -> list:
    """IPs marcadas en uso sin una VM viva detrás."""
    stale = []
    for ip in db.query(IpPool).filter(IpPool.is_used == 1).all():
        if ip.vm_id is None:
            stale.append(ip)
            continue
        vm = db.query(Vm).filter(Vm.id == ip.vm_id).first()
        if vm is None or vm.state in ("TERMINATED", "FAILED"):
            stale.append(ip)
    return stale


def _find_zombie_vms(db: Session, slices_by_id: dict) -> list:
    """VMs vivas cuyo slice ya llegó a un estado terminal (el cierre nunca se les propagó)."""
    zombies = []
    for vm in db.query(Vm).filter(Vm.state.in_(_ALIVE_VM_STATES), Vm.slice_id.isnot(None)).all():
        sl = slices_by_id.get(vm.slice_id)
        if sl is not None and sl.status in _TERMINAL_VM_STATE:
            zombies.append(vm)
    return zombies


def _find_terminated_vms(db: Session) -> list:
    """VMs en estado TERMINATED — historial de instancias ya destruidas."""
    return db.query(Vm).filter(Vm.state == "TERMINATED").all()


def _find_terminated_slices(db: Session) -> list:
    """Slices en estado TERMINATED — historial de despliegues ya destruidos."""
    return db.query(Slice).filter(Slice.status == "TERMINATED").all()


def _find_empty_drafts(db: Session) -> list:
    vm_slice_ids = {row[0] for row in db.query(Vm.slice_id).distinct().all() if row[0] is not None}
    return [s for s in db.query(Slice).filter(Slice.status == "DRAFT").all() if s.id not in vm_slice_ids]


def _find_stuck_operations(db: Session) -> list:
    """Solo lectura — nunca se corrige desde acá (ver docstring del módulo)."""
    now = datetime.utcnow()
    stuck = []
    for sl in db.query(Slice).filter(Slice.status.in_(("TERMINATING", "PROVISIONING"))).all():
        sj = _slice_json(sl)
        pending = sj.get("pending_destroy") or sj.get("pending_extension") or sj.get("pending_shrink")
        if not pending:
            if sl.status == "TERMINATING":
                stuck.append((sl, None))  # TERMINATING sin pending_destroy: ya de por sí inconsistente
            continue
        requested_at = _parse_date(pending.get("requested_at"))
        if requested_at is None or now - requested_at >= timedelta(minutes=STUCK_OP_TIMEOUT_MINUTES):
            stuck.append((sl, pending))
    return stuck


def scan(db: Session) -> dict:
    """Reporte de solo lectura — no modifica nada."""
    slice_ids = _existing_slice_ids(db)
    slices_by_id = {s.id: s for s in db.query(Slice).all()}

    orphan_vms = _find_orphan_vms(db, slice_ids)
    orphan_vlans = _find_orphan_vlans(db, slice_ids)
    stale_ips = _find_stale_ip_pool(db)
    zombies = _find_zombie_vms(db, slices_by_id)
    terminated = _find_terminated_vms(db)
    terminated_slices = _find_terminated_slices(db)
    empty_drafts = _find_empty_drafts(db)
    stuck_ops = _find_stuck_operations(db)

    return {
        "orphan_vms": {
            "count": len(orphan_vms),
            "items": [{"id": v.id, "name": v.name, "slice_id": v.slice_id, "state": v.state} for v in orphan_vms],
        },
        "orphan_vlans": {
            "count": len(orphan_vlans),
            "items": [{"id": v.id, "slice_id": v.slice_id, "type": v.type, "vlan_number": v.vlan_number} for v in orphan_vlans],
        },
        "stale_ip_pool": {
            "count": len(stale_ips),
            "items": [{"id": i.id, "ip_address": i.ip_address, "vm_id": i.vm_id} for i in stale_ips],
        },
        "zombie_vms": {
            "count": len(zombies),
            "items": [{"id": v.id, "name": v.name, "slice_id": v.slice_id, "state": v.state,
                       "slice_status": slices_by_id[v.slice_id].status} for v in zombies],
        },
        "terminated_vms": {
            "count": len(terminated),
            "items": [{"id": v.id, "name": v.name, "slice_id": v.slice_id} for v in terminated],
        },
        "terminated_slices": {
            "count": len(terminated_slices),
            "items": [{"id": s.id, "name": s.name, "creator_id": s.creator_id,
                       "date_destruction": s.date_destruction} for s in terminated_slices],
        },
        "empty_drafts": {
            "count": len(empty_drafts),
            "items": [{"id": s.id, "name": s.name, "creator_id": s.creator_id} for s in empty_drafts],
        },
        "stuck_operations": {
            "count": len(stuck_ops),
            "note": "Informativo — stuck_ops_scheduler ya los reintenta/revierte automáticamente; 'clean' no los toca.",
            "items": [{"id": s.id, "name": s.name, "status": s.status, "pending": bool(p)} for s, p in stuck_ops],
        },
    }


def clean(db: Session, categories: set = None, dry_run: bool = True) -> dict:
    """
    Aplica las correcciones de las categorías indicadas (todas por defecto).
    `stuck_operations` nunca se incluye acá — ver docstring del módulo.
    Con dry_run=True (default) solo cuenta lo que HARÍA, sin escribir en la BD.
    """
    selected = set(categories) if categories else set(CATEGORIES)
    unknown = selected - set(CATEGORIES)
    if unknown:
        raise ValueError(f"Categorías desconocidas: {sorted(unknown)}. Válidas: {list(CATEGORIES)}")

    slice_ids = _existing_slice_ids(db)
    slices_by_id = {s.id: s for s in db.query(Slice).all()}
    summary = {cat: 0 for cat in selected}

    if "orphan_vms" in selected:
        for vm in _find_orphan_vms(db, slice_ids):
            summary["orphan_vms"] += 1
            if not dry_run:
                if vm.external_ip:
                    ip = db.query(IpPool).filter(IpPool.ip_address == vm.external_ip).first()
                    if ip:
                        ip.is_used = 0
                        ip.vm_id = None
                db.delete(vm)

    if "orphan_vlans" in selected:
        for vlan in _find_orphan_vlans(db, slice_ids):
            summary["orphan_vlans"] += 1
            if not dry_run:
                db.delete(vlan)

    if "stale_ip_pool" in selected:
        for ip in _find_stale_ip_pool(db):
            summary["stale_ip_pool"] += 1
            if not dry_run:
                ip.is_used = 0
                ip.vm_id = None

    if "zombie_vms" in selected:
        for vm in _find_zombie_vms(db, slices_by_id):
            summary["zombie_vms"] += 1
            if not dry_run:
                if vm.external_ip:
                    ip = db.query(IpPool).filter(IpPool.ip_address == vm.external_ip).first()
                    if ip:
                        ip.is_used = 0
                        ip.vm_id = None
                vm.state = _TERMINAL_VM_STATE[slices_by_id[vm.slice_id].status]

    if "terminated_vms" in selected:
        for vm in _find_terminated_vms(db):
            summary["terminated_vms"] += 1
            if not dry_run:
                if vm.external_ip:
                    ip = db.query(IpPool).filter(IpPool.ip_address == vm.external_ip).first()
                    if ip:
                        ip.is_used = 0
                        ip.vm_id = None
                db.delete(vm)

    if "terminated_slices" in selected:
        for sl in _find_terminated_slices(db):
            summary["terminated_slices"] += 1
            if not dry_run:
                for vm in db.query(Vm).filter(Vm.slice_id == sl.id).all():
                    if vm.external_ip:
                        ip = db.query(IpPool).filter(IpPool.ip_address == vm.external_ip).first()
                        if ip:
                            ip.is_used = 0
                            ip.vm_id = None
                    db.delete(vm)
                db.query(Vlan).filter(Vlan.slice_id == sl.id).delete()
                db.delete(sl)

    if "empty_drafts" in selected:
        for sl in _find_empty_drafts(db):
            summary["empty_drafts"] += 1
            if not dry_run:
                db.delete(sl)

    if not dry_run:
        db.commit()
        logger.warning("[DB-MAINT] Limpieza aplicada: %s", summary)
    else:
        logger.info("[DB-MAINT] Dry-run: %s", summary)

    return {"dry_run": dry_run, "summary": summary}
