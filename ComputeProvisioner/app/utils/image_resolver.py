"""
Resolución de imágenes base para las VMs.

Actualmente las imágenes están presentes en todos los workers en una ruta fija.
Esta capa de abstracción permite migrar a una consulta a BD sin tocar el resto del código.

Para cambiar el comportamiento futuro, únicamente modificar `get_image_path`.
"""

import logging
from app.core.config import settings

logger = logging.getLogger(__name__)


def get_image_path(image_name: str, worker_ip: str) -> str:
    """
    Retorna la ruta absoluta de la imagen base en el worker indicado.

    Estado actual: las imágenes están pre-distribuidas en todos los workers
    bajo el directorio configurado en IMAGES_BASE_DIR.

    TODO (futuro): reemplazar el cuerpo de esta función por una consulta
    a la tabla `worker_images` en la BD, que registra qué imágenes están
    disponibles en cada worker. Ejemplo:
        record = db.query(WorkerImage).filter_by(
            worker_ip=worker_ip, image_name=image_name
        ).first()
        if not record:
            raise ImageNotFoundError(image_name, worker_ip)
        return record.path

    Args:
        image_name: nombre del archivo de imagen (ej: "ubuntu-22.04.qcow2")
        worker_ip:  IP del worker donde se va a desplegar la VM

    Returns:
        Ruta absoluta de la imagen en el worker.
    """
    path = f"{settings.IMAGES_BASE_DIR}/{image_name}"
    logger.debug(f"Resolviendo imagen '{image_name}' en worker {worker_ip} → {path}")
    return path


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
