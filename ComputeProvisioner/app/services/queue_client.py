"""
Cliente Redis para el módulo de colas.

Responsabilidades:
- Consumir mensajes de las queues de entrada (deploy / destroy).
- Publicar resultados a la queue de salida.
- Persistir el estado de VMs desplegadas por slice (para poder destruirlas luego).
"""

import json
import logging
from typing import List, Optional

import redis

from app.core.config import settings
from app.models.schemas import DeploySliceResponse, DestroySliceResponse

logger = logging.getLogger(__name__)

# Clave Redis donde se persisten las VMs de un slice desplegado
_SLICE_VMS_KEY = "deployed_vms:{slice_id}"


class QueueClient:

    def __init__(self):
        self._redis = redis.Redis(
            host=settings.REDIS_HOST,
            port=settings.REDIS_PORT,
            db=settings.REDIS_DB,
            decode_responses=True,
        )

    # ── Consumo ─────────────────────────────────────────────────────────────

    def consume_deploy(self, timeout: int = 5) -> Optional[dict]:
        """
        Bloquea hasta recibir un mensaje de la queue de deploy.
        Retorna el payload como dict, o None si timeout.
        """
        return self._blpop(settings.QUEUE_DEPLOY, timeout)

    def consume_destroy(self, timeout: int = 5) -> Optional[dict]:
        """
        Bloquea hasta recibir un mensaje de la queue de destroy.
        Retorna el payload como dict, o None si timeout.
        """
        return self._blpop(settings.QUEUE_DESTROY, timeout)

    # ── Publicación de resultados ────────────────────────────────────────────

    def publish_deploy_result(self, response: DeploySliceResponse) -> None:
        """
        Publica el resultado del despliegue al módulo de colas.
        También persiste las VMs exitosas para futuros destroy.
        """
        payload = response.model_dump()
        payload["event"] = "DEPLOY_RESULT"
        self._rpush(settings.QUEUE_RESULT, payload)
        logger.info(
            f"[slice={response.slice_id}] Resultado deploy publicado: "
            f"status={response.status}"
        )

        # Persistir VMs desplegadas exitosamente para poder destruirlas luego
        if response.vms:
            self._save_slice_vms(response.slice_id, [
                {
                    "vm_id":     vm.vm_id,
                    "worker_ip": vm.worker_ip,
                    "pid":       vm.pid,
                    "vnc_port":  vm.vnc_port,
                }
                for vm in response.vms
            ])

    def publish_destroy_result(self, response: DestroySliceResponse) -> None:
        """Publica el resultado de la destrucción al módulo de colas."""
        payload = response.model_dump()
        payload["event"] = "DESTROY_RESULT"
        self._rpush(settings.QUEUE_RESULT, payload)
        logger.info(
            f"[slice={response.slice_id}] Resultado destroy publicado: "
            f"status={response.status}"
        )

        # Limpiar estado persistido si todo fue destruido
        if response.status in ("success", "partial"):
            self._delete_slice_vms(response.slice_id)

    # ── Persistencia de estado ───────────────────────────────────────────────

    def _save_slice_vms(self, slice_id: str, vms: List[dict]) -> None:
        """Guarda el mapa VM→worker de un slice en Redis."""
        key = _SLICE_VMS_KEY.format(slice_id=slice_id)
        self._redis.set(key, json.dumps(vms))
        logger.debug(f"Estado de VMs guardado para slice {slice_id}")

    def get_slice_vms(self, slice_id: str) -> Optional[List[dict]]:
        """Recupera el mapa VM→worker de un slice desde Redis."""
        key = _SLICE_VMS_KEY.format(slice_id=slice_id)
        raw = self._redis.get(key)
        if raw:
            return json.loads(raw)
        return None

    def _delete_slice_vms(self, slice_id: str) -> None:
        key = _SLICE_VMS_KEY.format(slice_id=slice_id)
        self._redis.delete(key)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _blpop(self, queue: str, timeout: int) -> Optional[dict]:
        result = self._redis.blpop(queue, timeout=timeout)
        if result:
            _, raw = result
            return json.loads(raw)
        return None

    def _rpush(self, queue: str, payload: dict) -> None:
        self._redis.rpush(queue, json.dumps(payload))

    def ping(self) -> bool:
        try:
            return self._redis.ping()
        except Exception:
            return False
