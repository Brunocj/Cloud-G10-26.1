# app/routers/image_router.py
"""
Router para gestión de imágenes de VMs.

Funcionalidades:
  - GET  /utils/images          → Lista todas las imágenes
  - GET  /utils/images/unused   → Lista imágenes sin VMs activas (candidatas a borrar)
  - POST /utils/images/upload   → Sube un archivo .qcow2/.img al NFS y registra en BD
  - DELETE /utils/images/{id}   → Elimina imagen (verifica que no esté en uso)
"""

import logging
import os
import shutil
from datetime import datetime

import paramiko
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Image, Vm
from app.services.gc_scheduler import run_gc_cycle

logger = logging.getLogger("SliceManager.Images")

router = APIRouter(prefix="/api/v1/slices/utils/images", tags=["Image Management"])

# ── Configuración ────────────────────────────────────────────────────────────
IMAGES_DIR = os.getenv("IMAGES_DIR", "/mnt/cloud_images")

_WORKER_INVENTORY = {
    1: {"ip": "10.0.10.1", "user": "ubuntu", "key_path": "keys/worker1.pem"},
    2: {"ip": "10.0.10.2", "user": "ubuntu", "key_path": "keys/worker2.pem"},
    3: {"ip": "10.0.10.3", "user": "ubuntu", "key_path": "keys/worker3.pem"},
    4: {"ip": "10.0.10.4", "user": "ubuntu", "key_path": "keys/worker4.pem"},
}

# Estados que se consideran "activos" (la imagen no se puede borrar)
_ACTIVE_VM_STATES = ("DRAFT", "PROVISIONING", "ACTIVE", "PENDING_APPROVAL")


# ── Helpers SSH ──────────────────────────────────────────────────────────────

def _ssh_exec(worker: dict, command: str) -> tuple:
    """Ejecuta un comando en un worker remoto via SSH."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            worker["ip"],
            username=worker["user"],
            key_filename=worker["key_path"],
            timeout=15,
        )
        _, stdout, stderr = client.exec_command(command)
        exit_code = stdout.channel.recv_exit_status()
        return exit_code, stdout.read().decode(), stderr.read().decode()
    finally:
        client.close()


def _delete_file_via_ssh(file_path: str) -> None:
    """Borra un archivo en el NFS via SSH a server1."""
    worker = _WORKER_INVENTORY[1]
    try:
        code, _, err = _ssh_exec(worker, f"sudo rm -f {file_path}")
        if code != 0:
            logger.warning("rm falló para %s: %s", file_path, err)
    except Exception as exc:
        logger.warning("No se pudo borrar %s via SSH: %s", file_path, exc)


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("/", status_code=200)
def list_images(db: Session = Depends(get_db)):
    """Lista todas las imágenes con información de uso."""
    images = db.query(Image).filter(Image.is_general.isnot(None)).all()
    if not images:
        return []

    result = []
    for img in images:
        active_count = db.query(Vm).filter(
            Vm.image_id == img.id,
            Vm.state.in_(_ACTIVE_VM_STATES),
        ).count()

        result.append({
            "id": img.id,
            "name": img.name,
            "path": img.path,
            "is_general": img.is_general,
            "date_uploaded": img.date_uploaded,
            "in_use": active_count > 0,
            "active_vm_count": active_count,
        })
    return result


@router.get("/unused", status_code=200)
def get_unused_images(db: Session = Depends(get_db)):
    """Imágenes de usuario sin VMs activas — candidatas al GC."""
    used_ids_query = (
        db.query(Vm.image_id)
        .filter(Vm.image_id.isnot(None), Vm.state.in_(_ACTIVE_VM_STATES))
        .distinct()
    )

    unused = (
        db.query(Image)
        .filter(Image.id.notin_(used_ids_query), Image.is_general == 0)
        .all()
    )

    return [
        {
            "id": img.id,
            "name": img.name,
            "path": img.path,
            "date_uploaded": img.date_uploaded,
        }
        for img in unused
    ]


@router.post("/upload", status_code=201)
async def upload_image(
    name: str = Form(...),
    is_general: int = Form(0),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Sube un archivo de imagen al NFS y lo registra en BD."""
    allowed = (".qcow2", ".img", ".iso")
    if not any(file.filename.lower().endswith(ext) for ext in allowed):
        raise HTTPException(
            status_code=400,
            detail=f"Formato no soportado. Use: {', '.join(allowed)}",
        )

    safe_filename = file.filename.replace(" ", "_")
    dest_path = os.path.join(IMAGES_DIR, safe_filename)

    existing = db.query(Image).filter(Image.path == dest_path).first()
    if existing:
        if existing.is_general is None:
            # Imagen soft-deleted: reutilizamos el registro y sobreescribimos el archivo
            logger.info("Imagen '%s' estaba soft-deleted — se reutiliza el registro (id=%d)", name, existing.id)
            # El archivo se sobreescribirá más abajo
        else:
            raise HTTPException(
                status_code=409,
                detail=f"Ya existe una imagen activa registrada en esa ruta: {dest_path}",
            )

    try:
        os.makedirs(IMAGES_DIR, exist_ok=True)
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except OSError as exc:
        logger.error("Error guardando imagen en %s: %s", dest_path, exc)
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo guardar el archivo. ¿El volumen NFS está montado? ({IMAGES_DIR})",
        )

    if existing and existing.is_general is None:
        # Reactivar registro soft-deleted
        existing.name = name
        existing.is_general = is_general
        existing.date_uploaded = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        db.commit()
        db.refresh(existing)
        logger.info("Imagen '%s' reactivada → %s", name, dest_path)
        return {
            "id": existing.id,
            "name": existing.name,
            "path": dest_path,
            "message": f"Imagen '{name}' reactivada correctamente.",
        }

    nueva_imagen = Image(
        user_id="user-123",
        name=name,
        date_uploaded=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        is_general=is_general,
        path=dest_path,
    )
    db.add(nueva_imagen)
    db.commit()
    db.refresh(nueva_imagen)

    logger.info("Imagen '%s' subida → %s", name, dest_path)
    return {
        "id": nueva_imagen.id,
        "name": nueva_imagen.name,
        "path": dest_path,
        "message": f"Imagen '{name}' registrada correctamente.",
    }


@router.delete("/{image_id}", status_code=200)
def delete_image(image_id: int, db: Session = Depends(get_db)):
    """
    Elimina una imagen de disco y BD.
    Bloquea si hay VMs activas usando la imagen o si es imagen base.
    """
    img = db.query(Image).filter(Image.id == image_id).first()
    if not img:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")

    if img.is_general == 1:
        raise HTTPException(
            status_code=403,
            detail="Las imágenes base del sistema no pueden eliminarse.",
        )

    active_vms = (
        db.query(Vm)
        .filter(Vm.image_id == image_id, Vm.state.in_(_ACTIVE_VM_STATES))
        .count()
    )
    if active_vms > 0:
        raise HTTPException(
            status_code=409,
            detail=f"La imagen está en uso por {active_vms} VM(s) activa(s).",
        )

    # Borramos el archivo físico
    if img.path:
        if os.path.exists(img.path):
            try:
                os.remove(img.path)
                logger.info("Archivo %s eliminado directamente.", img.path)
            except OSError:
                logger.warning("Borrado directo falló, intentando via SSH...")
                _delete_file_via_ssh(img.path)
        else:
            _delete_file_via_ssh(img.path)

    # Soft-delete: marcamos is_general=NULL para preservar FK con vms históricos
    img.is_general = None
    db.commit()

    logger.info("Imagen id=%d '%s' marcada como eliminada.", image_id, img.name)
    return {"message": f"Imagen '{img.name}' eliminada correctamente."}


@router.post("/gc/run", status_code=200)
def run_gc_now(background_tasks: BackgroundTasks):
    """
    Dispara un ciclo de Garbage Collection inmediatamente en background.
    Limpia ISOs de cloud-init y discos QCOW2 huerfanos en todos los workers.
    """
    background_tasks.add_task(run_gc_cycle)
    logger.info("[GC] Ciclo manual disparado desde la API.")
    return {"message": "Ciclo de GC iniciado en background. Revisa los logs para ver el resultado."}

