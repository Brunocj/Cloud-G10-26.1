"""
Resolución de imágenes base para las VMs.

Patrón Strategy por availability_zone_id:
  · Linux Cluster (az_id=1) → retorna ruta física en el worker (/images/...)
  · OpenStack     (az_id=2) → consulta Glance vía openstacksdk y retorna UUID

Para cambiar el comportamiento futuro, únicamente modificar `get_image_path`.
"""

import logging
import os
from app.core.config import settings

logger = logging.getLogger(__name__)

# ID de AZ por convención (configurable via env)
AZ_ID_OPENSTACK = int(os.getenv("OPENSTACK_AZ_ID", "2"))


def get_image_path(image_name: str, worker_ip: str, availability_zone_id: int = 1) -> str:
    """
    Retorna el identificador de la imagen base según la zona de disponibilidad.

    Strategy:
      · Linux Cluster → ruta absoluta en el filesystem del worker
      · OpenStack     → UUID de Glance (string)

    Args:
        image_name:           nombre del archivo de imagen (ej: "ubuntu-22.04.qcow2")
        worker_ip:            IP del worker donde se va a desplegar la VM
        availability_zone_id: ID de la zona de disponibilidad

    Returns:
        Ruta absoluta (Linux) o UUID de Glance (OpenStack).
    """
    if availability_zone_id == AZ_ID_OPENSTACK:
        return _resolve_glance_uuid(image_name)

    # ── Strategy: Linux Cluster (comportamiento original) ──
    path = f"{settings.IMAGES_BASE_DIR}/{image_name}"
    logger.debug(f"Resolviendo imagen '{image_name}' en worker {worker_ip} → {path}")
    return path


def _resolve_glance_uuid(image_name: str) -> str:
    """
    Consulta Glance vía openstacksdk para encontrar el UUID de una imagen.
    Intenta coincidencia exacta, luego por nombre sin extensión, luego parcial.

    Retorna el UUID como string (NO descarga el archivo).
    """
    import openstack  # openstacksdk — nunca SSH

    conn = openstack.connect(
        auth_url=os.getenv("OS_AUTH_URL"),
        username=os.getenv("OS_USERNAME", "admin"),
        password=os.getenv("OS_PASSWORD", ""),
        project_name=os.getenv("OS_PROJECT_NAME", "admin"),
        user_domain_name=os.getenv("OS_USER_DOMAIN_NAME", "Default"),
        project_domain_name=os.getenv("OS_PROJECT_DOMAIN_NAME", "Default"),
    )

    base_name = os.path.basename(image_name)
    name_no_ext = os.path.splitext(base_name)[0]

    # 1. Coincidencia exacta por nombre de archivo
    img = conn.image.find_image(base_name)
    if img:
        logger.info(f"[Glance] Imagen encontrada (exacta): '{base_name}' → {img.id}")
        return img.id

    # 2. Coincidencia por nombre sin extensión
    img = conn.image.find_image(name_no_ext)
    if img:
        logger.info(f"[Glance] Imagen encontrada (sin ext): '{name_no_ext}' → {img.id}")
        return img.id

    # 3. Coincidencia parcial (subcadena)
    for glance_img in conn.image.images():
        if base_name.lower() in glance_img.name.lower() or name_no_ext.lower() in glance_img.name.lower():
            logger.info(f"[Glance] Imagen encontrada (parcial): '{glance_img.name}' → {glance_img.id}")
            return glance_img.id

    raise RuntimeError(
        f"No se pudo encontrar ninguna imagen en Glance que coincida con: {image_name}"
    )


def get_vm_disk_path(vm_id: str, slice_id: str) -> str:
    """
    Retorna la ruta donde se creará el disco thin (QCOW2 con backing file)
    de una VM específica en el worker.

    Args:
        vm_id:    ID de la VM
        slice_id: ID del slice al que pertenece

    Returns:
        Ruta del disco thin en el worker.
    """
    return f"{settings.VMS_BASE_DIR}/{vm_id}-{slice_id}.qcow2"

