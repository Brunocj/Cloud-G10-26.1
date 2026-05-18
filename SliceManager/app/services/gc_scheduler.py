# app/services/gc_scheduler.py
"""
Garbage Collector Scheduler para la plataforma PUCP Cloud.

Corre en background cada GC_INTERVAL_HOURS horas y limpia:
  1. ISOs de cloud-init (/vms/*_seed.iso) en todos los workers.
  2. Discos QCOW2 huerfanos (/vms/*.qcow2) cuya VM ya no existe activa en BD.
"""
import asyncio
import logging
import os
import re

import paramiko

from app.database import SessionLocal
from app.models import Image, Vm

logger = logging.getLogger("SliceManager.GC")

GC_INTERVAL_HOURS: float = float(os.getenv("GC_INTERVAL_HOURS", "6"))

_WORKER_INVENTORY = {
    1: {"ip": "10.0.10.1", "user": "ubuntu", "key_path": "keys/worker1.pem"},
    2: {"ip": "10.0.10.2", "user": "ubuntu", "key_path": "keys/worker2.pem"},
    3: {"ip": "10.0.10.3", "user": "ubuntu", "key_path": "keys/worker3.pem"},
    4: {"ip": "10.0.10.4", "user": "ubuntu", "key_path": "keys/worker4.pem"},
}

VMS_DIR = "/vms"
_ALIVE_STATES = ("DRAFT", "PROVISIONING", "ACTIVE", "PENDING_APPROVAL")


def _ssh_delete_image_file(file_path: str) -> None:
    """Intenta borrar un archivo de imagen via SSH al server1 (donde está el NFS)."""
    worker = _WORKER_INVENTORY.get(1)
    if not worker:
        return
    try:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        client.connect(worker["ip"], username=worker["user"], key_filename=worker["key_path"], timeout=15)
        _, stdout, stderr = client.exec_command(f"sudo rm -f {file_path}")
        exit_code = stdout.channel.recv_exit_status()
        client.close()
        if exit_code == 0:
            logger.info("[GC] Archivo borrado via SSH: %s", file_path)
        else:
            logger.warning("[GC] rm via SSH falló para %s: %s", file_path, stderr.read().decode())
    except Exception as exc:
        logger.warning("[GC] SSH delete falló para %s: %s", file_path, exc)


def _ssh_exec(worker: dict, command: str) -> tuple:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(worker["ip"], username=worker["user"], key_filename=worker["key_path"], timeout=15)
        _, stdout, stderr = client.exec_command(command)
        exit_code = stdout.channel.recv_exit_status()
        return exit_code, stdout.read().decode().strip(), stderr.read().decode().strip()
    finally:
        client.close()


def _gc_seed_isos(worker_id: int, worker: dict) -> int:
    logger.info("[GC] Worker %d: limpiando ISOs de cloud-init...", worker_id)
    code, out, _ = _ssh_exec(worker, f"sudo ls {VMS_DIR}/*_seed.iso 2>/dev/null")
    if code != 0 or not out:
        return 0
    iso_files = [f.strip() for f in out.splitlines() if f.strip()]
    deleted = 0
    for iso in iso_files:
        c, _, e = _ssh_exec(worker, f"sudo rm -f {iso}")
        if c == 0:
            logger.info("[GC] Worker %d: borrado %s", worker_id, iso)
            deleted += 1
        else:
            logger.warning("[GC] Worker %d: no se pudo borrar %s: %s", worker_id, iso, e)
    return deleted


def _gc_orphan_disks(worker_id: int, worker: dict, alive_vm_names: set) -> int:
    logger.info("[GC] Worker %d: buscando discos huerfanos...", worker_id)
    code, out, _ = _ssh_exec(worker, f"sudo ls {VMS_DIR}/*.qcow2 2>/dev/null")
    if code != 0 or not out:
        return 0
    disk_files = [f.strip() for f in out.splitlines() if f.strip()]
    deleted = 0
    pattern = re.compile(r"/vms/(.+)\.qcow2$")
    for disk in disk_files:
        m = pattern.match(disk)
        if not m:
            continue
        disk_key = m.group(1)
        if disk_key not in alive_vm_names:
            c, _, e = _ssh_exec(worker, f"sudo rm -f {disk}")
            if c == 0:
                logger.info("[GC] Worker %d: disco huerfano borrado %s", worker_id, disk)
                deleted += 1
            else:
                logger.warning("[GC] Worker %d: no se pudo borrar %s: %s", worker_id, disk, e)
    return deleted


def run_gc_cycle() -> dict:
    logger.info("[GC] Iniciando ciclo de Garbage Collection...")
    db = SessionLocal()
    summary = {"isos_deleted": 0, "orphan_disks_deleted": 0, "unused_images_deleted": 0, "workers_failed": 0}
    try:
        alive_vms = db.query(Vm).filter(Vm.state.in_(_ALIVE_STATES)).all()
        alive_vm_names = {f"{vm.name}-{vm.slice_id}" for vm in alive_vms}
        logger.info("[GC] VMs activas en BD: %d", len(alive_vm_names))
    except Exception as exc:
        logger.error("[GC] Error consultando VMs: %s", exc)
        db.close()
        return summary

    # Paso 1 y 2: Limpiar ISOs y discos huérfanos en workers
    for worker_id, worker in _WORKER_INVENTORY.items():
        try:
            isos = _gc_seed_isos(worker_id, worker)
            orphans = _gc_orphan_disks(worker_id, worker, alive_vm_names)
            summary["isos_deleted"] += isos
            summary["orphan_disks_deleted"] += orphans
        except Exception as exc:
            summary["workers_failed"] += 1
            logger.error("[GC] Worker %d fallo: %s", worker_id, exc)

    # Paso 3: Eliminar imágenes de usuario sin VMs activas
    try:
        # IDs de imágenes que están siendo usadas por alguna VM activa
        used_image_ids = {
            row[0] for row in
            db.query(Vm.image_id).filter(Vm.image_id.isnot(None), Vm.state.in_(_ALIVE_STATES)).distinct().all()
        }
        # Imágenes de usuario (no base) que NO están en uso
        unused_images = db.query(Image).filter(
            Image.is_general == 0,
            Image.id.notin_(used_image_ids) if used_image_ids else True
        ).all()

        for img in unused_images:
            # Borrar archivo físico
            if img.path:
                try:
                    if os.path.exists(img.path):
                        os.remove(img.path)
                        logger.info("[GC] Archivo de imagen borrado: %s", img.path)
                    else:
                        # Intentar via SSH por si el NFS está montado en otro nodo
                        _ssh_delete_image_file(img.path)
                except OSError as exc:
                    logger.warning("[GC] No se pudo borrar archivo %s: %s", img.path, exc)
                    _ssh_delete_image_file(img.path)

            # Borrar registro de BD
            db.delete(img)
            summary["unused_images_deleted"] += 1
            logger.info("[GC] Imagen sin uso eliminada: id=%d name='%s'", img.id, img.name)

        if unused_images:
            db.commit()

    except Exception as exc:
        logger.error("[GC] Error limpiando imágenes sin uso: %s", exc)
        db.rollback()
    finally:
        db.close()

    logger.info("[GC] Ciclo completado. Resumen: %s", summary)
    return summary


async def gc_scheduler_task():
    logger.info("[GC] Scheduler iniciado. Intervalo: %.1f horas.", GC_INTERVAL_HOURS)
    while True:
        await asyncio.sleep(GC_INTERVAL_HOURS * 3600)
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, run_gc_cycle)
        except Exception as exc:
            logger.error("[GC] Error en scheduler: %s", exc)
