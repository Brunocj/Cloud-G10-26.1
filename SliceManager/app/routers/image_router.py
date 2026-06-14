# app/routers/image_router.py
"""
Router para gestión de imágenes de VMs.

Funcionalidades:
  - GET  /utils/images          → Lista catálogo híbrido (BD local + OpenStack Glance)
  - GET  /utils/images/unused   → Lista imágenes sin VMs activas (candidatas a borrar)
  - POST /utils/images/upload   → Sube un archivo .qcow2/.img al NFS y registra en BD
  - DELETE /utils/images/{id}   → Elimina imagen (verifica que no esté en uso)
"""

import logging
import os
import shutil
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import List

import paramiko
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import AvailabilityZone, Image, Vm
from app.schemas import ImageResponse
from app.services.gc_scheduler import run_gc_cycle
from app.auth import CurrentUser, get_current_user, require_roles

logger = logging.getLogger("SliceManager.Images")

router = APIRouter(prefix="/api/v1/slices/utils/images", tags=["Image Management"])

# ── Configuración ────────────────────────────────────────────────────────────
IMAGES_DIR = os.getenv("IMAGES_DIR", "/mnt/cloud_images")

GATEWAY_IP = "10.20.11.119"
_KEY = "/app/keys/id_ed25519"  # ruta absoluta — montada vía docker volume
_WORKER_INVENTORY = {
    1: {"ip": GATEWAY_IP, "port": 5811, "user": "ubuntu", "key_path": _KEY},
    2: {"ip": GATEWAY_IP, "port": 5812, "user": "ubuntu", "key_path": _KEY},
    3: {"ip": GATEWAY_IP, "port": 5813, "user": "ubuntu", "key_path": _KEY},
    4: {"ip": GATEWAY_IP, "port": 5814, "user": "ubuntu", "key_path": _KEY},
}

# Estados que se consideran "activos" (la imagen no se puede borrar)
_ACTIVE_VM_STATES = ("DRAFT", "PROVISIONING", "ACTIVE", "PENDING_APPROVAL")

# ID de zona OpenStack (se asume id=2 si no se configura via env)
OPENSTACK_AZ_ID = int(os.getenv("OPENSTACK_AZ_ID", "2"))


# ── Helpers SSH ──────────────────────────────────────────────────────────────

def _ssh_exec(worker: dict, command: str) -> tuple:
    """Ejecuta un comando en un worker remoto via SSH."""
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            worker["ip"],
            port=worker.get("port", 22),
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


# ── OpenStack Glance helper ───────────────────────────────────────────────────

def _fetch_openstack_images() -> List[dict]:
    """
    Se conecta a OpenStack Glance usando openstacksdk y devuelve la lista
    de imágenes disponibles como dicts normalizados.

    Lee las credenciales desde variables de entorno:
      OS_AUTH_URL, OS_USERNAME, OS_PASSWORD, OS_PROJECT_NAME,
      OS_USER_DOMAIN_NAME, OS_PROJECT_DOMAIN_NAME

    Si OpenStack está caído o faltan credenciales, retorna lista vacía
    y loguea el error (no lanza excepción — fail-safe).
    """
    try:
        import openstack  # openstacksdk

        auth_url = os.getenv("OS_AUTH_URL")
        if not auth_url:
            logger.warning("[OpenStack] OS_AUTH_URL no configurado — omitiendo imágenes de OpenStack")
            return []

        conn = openstack.connect(
            auth_url=auth_url,
            username=os.getenv("OS_USERNAME", "admin"),
            password=os.getenv("OS_PASSWORD", ""),
            project_name=os.getenv("OS_PROJECT_NAME", "admin"),
            user_domain_name=os.getenv("OS_USER_DOMAIN_NAME", "Default"),
            project_domain_name=os.getenv("OS_PROJECT_DOMAIN_NAME", "Default"),
        )

        images = []
        for img in conn.image.images():
            images.append({
                "name":       img.name,
                "os_id":      img.id,           # UUID de Glance
                "status":     img.status,
                "size":       img.size,
                "visibility": getattr(img, "visibility", "private"),
            })

        logger.info("[OpenStack] Glance devolvió %d imágenes", len(images))
        return images

    except Exception as exc:
        logger.error("[OpenStack] Error conectando a Glance: %s", exc)
        return []

def _upload_openstack_image(name: str, filepath: str, is_general: int) -> str:
    """
    Sube un archivo local a OpenStack Glance y devuelve el UUID generado.
    Usa visibilidad 'private' porque el control de acceso global (is_general)
    se gestiona en nuestra propia BD, y OpenStack solo requiere acceso del admin.
    """
    import openstack

    auth_url = os.getenv("OS_AUTH_URL")
    if not auth_url:
        raise ValueError("OS_AUTH_URL no configurado")

    conn = openstack.connect(
        auth_url=auth_url,
        username=os.getenv("OS_USERNAME", "admin"),
        password=os.getenv("OS_PASSWORD", ""),
        project_name=os.getenv("OS_PROJECT_NAME", "admin"),
        user_domain_name=os.getenv("OS_USER_DOMAIN_NAME", "Default"),
        project_domain_name=os.getenv("OS_PROJECT_DOMAIN_NAME", "Default"),
    )

    # Creamos el registro en Glance (visibility='private' por defecto es suficiente)
    logger.info("[OpenStack] Creando imagen en Glance: %s", name)
    image = conn.image.create_image(
        name=name,
        disk_format='qcow2',
        container_format='bare',
        visibility='private'
    )

    # Subimos el archivo binario
    logger.info("[OpenStack] Subiendo archivo a Glance (UUID=%s)...", image.id)
    with open(filepath, 'rb') as f:
        conn.image.upload_image(image.id, f)
    
    logger.info("[OpenStack] Subida a Glance finalizada exitosamente.")
    return image.id

def _delete_openstack_image(glance_id: str) -> None:
    """
    Elimina una imagen de OpenStack Glance por su UUID.
    """
    import openstack

    auth_url = os.getenv("OS_AUTH_URL")
    if not auth_url:
        logger.warning("[OpenStack] OS_AUTH_URL no configurado, no se borrará la imagen %s de Glance", glance_id)
        return

    try:
        conn = openstack.connect(
            auth_url=auth_url,
            username=os.getenv("OS_USERNAME", "admin"),
            password=os.getenv("OS_PASSWORD", ""),
            project_name=os.getenv("OS_PROJECT_NAME", "admin"),
            user_domain_name=os.getenv("OS_USER_DOMAIN_NAME", "Default"),
            project_domain_name=os.getenv("OS_PROJECT_DOMAIN_NAME", "Default"),
        )
        logger.info("[OpenStack] Eliminando imagen %s de Glance...", glance_id)
        conn.image.delete_image(glance_id)
        logger.info("[OpenStack] Imagen eliminada de Glance.")
    except Exception as exc:
        logger.error("[OpenStack] Error eliminando imagen de Glance: %s", exc)


# ── Endpoints ────────────────────────────────────────────────────────────────

@router.get("", status_code=200, response_model=List[ImageResponse])
def list_images(db: Session = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    """
    Catálogo híbrido de imágenes.

    1. Consulta la BD local (con JOIN a AvailabilityZone para saber el az_name).
    2. Consulta Glance de OpenStack en un thread-pool (no bloquea el event loop).
    3. Para cada imagen de Glance que NO exista ya en la BD (por nombre),
       crea un registro efímero en memoria (no en BD) con az_name="OpenStack".
    4. Si OpenStack falla, se retorna igualmente con las imágenes locales.
    """
    # ── 1. Imágenes desde la BD local ────────────────────────────────────────
    if current_user.can_manage_all():
        db_images = (
            db.query(Image)
            .options(joinedload(Image.availability_zone))
            .filter(Image.is_general.isnot(None))
            .all()
        )
    else:
        db_images = (
            db.query(Image)
            .options(joinedload(Image.availability_zone))
            .filter(
                Image.is_general.isnot(None),
                (Image.is_general == 1) | (Image.user_id == current_user.user_id),
            )
            .all()
        )

    result = []
    db_image_names = set()

    for img in db_images:
        active_count = db.query(Vm).filter(
            Vm.image_id == img.id,
            Vm.state.in_(_ACTIVE_VM_STATES),
        ).count()

        az_name = img.availability_zone.name if img.availability_zone else None

        result.append(ImageResponse(
            id=img.id,
            name=img.name,
            availability_zone_id=img.availability_zone_id,
            az_name=az_name,
            path=img.path,
            is_general=img.is_general,
            date_uploaded=img.date_uploaded,
            in_use=active_count > 0,
            active_vm_count=active_count,
        ))
        db_image_names.add(img.name)

    # ── 2. Imágenes desde OpenStack Glance (en thread-pool) ─────────────────
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(_fetch_openstack_images)
            os_images = future.result(timeout=10)
    except Exception as exc:
        logger.error("[OpenStack] Timeout o error en thread-pool Glance: %s", exc)
        os_images = []

    # ── 3. Sincronización: añadir imágenes de OpenStack no presentes en BD ───
    # Buscamos el AZ de OpenStack en la BD para obtener su nombre oficial
    os_az = db.query(AvailabilityZone).filter(AvailabilityZone.id == OPENSTACK_AZ_ID).first()
    os_az_name = os_az.name if os_az else "OpenStack"

    new_images_synced = 0
    for os_img in os_images:
        if os_img["name"] not in db_image_names and os_img.get("status") == "active":
            is_gen = 1 if os_img.get("visibility") == "public" else 0
            
            nueva_imagen = Image(
                name=os_img["name"],
                user_id="system_sync",
                date_uploaded=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                is_general=is_gen,
                path=os_img["os_id"], # Glance UUID
                availability_zone_id=OPENSTACK_AZ_ID
            )
            db.add(nueva_imagen)
            db.flush() # Para obtener el ID generado

            # Si el current_user no puede ver todas y la imagen es privada, no la añadimos al result (sólo se sincroniza en BD)
            if is_gen == 0 and not current_user.can_manage_all():
                new_images_synced += 1
                continue

            result.append(ImageResponse(
                id=nueva_imagen.id,
                name=nueva_imagen.name,
                availability_zone_id=OPENSTACK_AZ_ID,
                az_name=os_az_name,
                path=nueva_imagen.path,
                is_general=is_gen,
                date_uploaded=nueva_imagen.date_uploaded,
                in_use=False,
                active_vm_count=0,
            ))
            new_images_synced += 1
            
    if new_images_synced > 0:
        db.commit()

    logger.info("[Images] Catálogo híbrido: %d imágenes BD + %d nuevas de OpenStack (total=%d)",
                len(db_images), new_images_synced, len(result))
    return result


@router.get("/unused", status_code=200)
def get_unused_images(db: Session = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    """Imágenes de usuario sin VMs activas — candidatas al GC (filtrado por dueño)."""
    used_ids_query = (
        db.query(Vm.image_id)
        .filter(Vm.image_id.isnot(None), Vm.state.in_(_ACTIVE_VM_STATES))
        .distinct()
    )

    if current_user.can_manage_all():
        unused = (
            db.query(Image)
            .filter(Image.id.notin_(used_ids_query), Image.is_general == 0)
            .all()
        )
    else:
        unused = (
            db.query(Image)
            .filter(
                Image.id.notin_(used_ids_query),
                Image.is_general == 0,
                Image.user_id == current_user.user_id
            )
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
    availability_zone_id: int = Form(1),  # Por defecto: Linux Cluster (id=1)
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: CurrentUser = Depends(get_current_user),
):
    """Sube un archivo de imagen al NFS y lo registra en BD, asociado a una AZ."""
    # Validación de rol: solo admins/superadmins pueden crear imágenes generales
    if is_general == 1 and not current_user.can_manage_all():
        raise HTTPException(
            status_code=403,
            detail="Solo administradores pueden registrar imágenes generales del sistema.",
        )

    allowed = (".qcow2", ".img", ".iso")
    if not any(file.filename.lower().endswith(ext) for ext in allowed):
        raise HTTPException(
            status_code=400,
            detail=f"Formato no soportado. Use: {', '.join(allowed)}",
        )

    safe_filename = file.filename.replace(" ", "_")
    
    if availability_zone_id == OPENSTACK_AZ_ID:
        import uuid
        dest_path = f"/tmp/os_upload_{uuid.uuid4().hex}_{safe_filename}"
        existing = None # No chequear path porque es temporal en /tmp
    else:
        dest_path = os.path.join(IMAGES_DIR, safe_filename)
        existing = db.query(Image).filter(Image.path == dest_path).first()
        if existing:
            if existing.is_general is None:
                # Imagen soft-deleted: reutilizamos el registro y sobreescribimos el archivo
                logger.info("Imagen '%s' estaba soft-deleted — se reutiliza el registro (id=%d)", name, existing.id)
            else:
                raise HTTPException(
                    status_code=409,
                    detail=f"Ya existe una imagen activa registrada en esa ruta: {dest_path}",
                )

    try:
        if availability_zone_id != OPENSTACK_AZ_ID:
            os.makedirs(IMAGES_DIR, exist_ok=True)
        with open(dest_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
    except OSError as exc:
        logger.error("Error guardando imagen en %s: %s", dest_path, exc)
        raise HTTPException(
            status_code=500,
            detail=f"No se pudo guardar el archivo localmente en {dest_path}",
        )

    # Si es para OpenStack (OPENSTACK_AZ_ID), subimos a Glance y no guardamos permanentemente en local
    final_path = dest_path
    if availability_zone_id == OPENSTACK_AZ_ID:
        try:
            glance_id = _upload_openstack_image(name, dest_path, is_general)
            final_path = f"glance://{glance_id}"
            
            # Limpiamos el archivo local que solo servía como puente temporal
            os.remove(dest_path)
            logger.info("Archivo local temporal eliminado: %s", dest_path)
        except Exception as exc:
            # Limpiamos en caso de error
            if os.path.exists(dest_path):
                os.remove(dest_path)
            logger.error("Error subiendo a Glance: %s", exc)
            raise HTTPException(status_code=500, detail=f"Error subiendo imagen a OpenStack Glance: {exc}")

    if existing and existing.is_general is None:
        if existing.user_id != current_user.user_id and not current_user.can_manage_all():
            raise HTTPException(
                status_code=403,
                detail="No tiene permisos para modificar o reactivar esta imagen.",
            )
        existing.name = name
        existing.is_general = is_general
        existing.user_id = current_user.user_id
        existing.date_uploaded = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        existing.availability_zone_id = availability_zone_id
        existing.path = final_path
        db.commit()
        db.refresh(existing)
        logger.info("Imagen '%s' reactivada → %s", name, final_path)
        return {
            "id": existing.id,
            "name": existing.name,
            "path": final_path,
            "message": f"Imagen '{name}' reactivada correctamente.",
        }

    nueva_imagen = Image(
        user_id=current_user.user_id,
        name=name,
        date_uploaded=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S"),
        is_general=is_general,
        path=final_path,
        availability_zone_id=availability_zone_id,
    )
    db.add(nueva_imagen)
    db.commit()
    db.refresh(nueva_imagen)

    logger.info("Imagen '%s' subida → %s (AZ=%d)", name, final_path, availability_zone_id)
    return {
        "id": nueva_imagen.id,
        "name": nueva_imagen.name,
        "path": final_path,
        "message": f"Imagen '{name}' registrada correctamente.",
    }


@router.delete("/{image_id}", status_code=200)
def delete_image(image_id: int, db: Session = Depends(get_db), current_user: CurrentUser = Depends(get_current_user)):
    """
    Elimina una imagen de disco y BD.
    Bloquea si hay VMs activas usando la imagen o si es imagen base.
    """
    img = db.query(Image).filter(Image.id == image_id).first()
    if not img:
        raise HTTPException(status_code=404, detail="Imagen no encontrada")

    # Validar propiedad (solo dueño o administradores)
    if img.user_id != current_user.user_id and not current_user.can_manage_all():
        raise HTTPException(
            status_code=403,
            detail="No tiene permisos para eliminar esta imagen de otro usuario.",
        )

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

    # Borramos el archivo físico o llamamos a Glance
    if img.path:
        if img.path.startswith("glance://"):
            glance_id = img.path.split("://")[1]
            _delete_openstack_image(glance_id)
        else:
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
def run_gc_now(
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(require_roles("admin", "superAdmin")),
):
    """
    Dispara un ciclo de Garbage Collection inmediatamente en background.
    Limpia ISOs de cloud-init y discos QCOW2 huerfanos en todos los workers.
    """
    background_tasks.add_task(run_gc_cycle)
    logger.info("[GC] Ciclo manual disparado desde la API por %s.", current_user.user_id)
    return {"message": "Ciclo de GC iniciado en background. Revisa los logs para ver el resultado."}
