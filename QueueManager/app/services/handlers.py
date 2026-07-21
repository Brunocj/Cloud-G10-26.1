"""
Handlers de mensajes NATS.
Cada handler corresponde a un subject al que el Queue Manager está suscrito.
Deserializa el mensaje y lo pasa al Orchestrator.

Contrato de ACK
---------------
Los subjects `slice.*` son consumidores JetStream durables con `manual_ack=True`.
El ACK se envía DESPUÉS de procesar, nunca antes: si se ACKea al entrar y el
proceso muere a mitad de la saga, JetStream da el mensaje por consumido, nadie
lo reprocesa y nadie publica un resultado — el Slice Manager se queda esperando
para siempre (slice colgado en PROVISIONING/TERMINATING). Con el ACK diferido,
una caída deja el mensaje sin confirmar y JetStream lo reentrega solo.

Como la saga puede tardar minutos y el `ack_wait` por defecto es de 30 s, cada
handler levanta un heartbeat que llama a `msg.in_progress()` periódicamente para
que JetStream no considere el mensaje abandonado mientras sigue corriendo.

Un payload inválido (poison message) sí se ACKea: reintentarlo nunca va a
funcionar. En ese caso se publica un resultado ERROR en `slice.result` para que
el Slice Manager saque al slice de su estado transitorio en vez de esperar una
confirmación que no va a llegar.
"""

import asyncio
import json
import logging

from nats.aio.msg import Msg

from app.core.config import settings
from app.models.schemas import DeploySliceRequest, DestroySliceRequest
from app.services.nats_client import nats_manager
from app.services.orchestrator import Orchestrator

logger = logging.getLogger(__name__)

_orchestrator = Orchestrator()

# Cada cuánto renovar el ack_wait mientras la saga sigue corriendo.
_ACK_PROGRESS_INTERVAL_SECONDS = 15


async def _keep_ack_alive(msg: Msg, subject: str) -> None:
    """Renueva el ack_wait del mensaje mientras el handler sigue trabajando."""
    while True:
        await asyncio.sleep(_ACK_PROGRESS_INTERVAL_SECONDS)
        try:
            await msg.in_progress()
        except Exception as exc:
            logger.warning("[QM] No se pudo renovar el ACK de '%s': %s", subject, exc)
            return


async def _publish_error_result(payload: dict, operation: str, reason: str) -> None:
    """
    Avisa al Slice Manager de un fallo que ocurrió ANTES de entrar al Orchestrator
    (payload corrupto/inválido), donde `_fail_deploy`/`_fail_destroy` nunca corren.
    Sin esto el slice queda colgado en su estado transitorio.
    """
    slice_id   = str(payload.get("slice_id", "")) if payload else ""
    request_id = str(payload.get("request_id", "")) if payload else ""
    if not slice_id:
        logger.error("[QM] Payload de '%s' sin slice_id — imposible notificar el fallo.", operation)
        return
    try:
        await nats_manager.publish(settings.SUBJECT_RESULT, {
            "slice_id":   slice_id,
            "request_id": request_id,
            "status":     "error",
            "error":      reason,
        })
        logger.info("[QM] ⚠️ Resultado ERROR publicado para slice %s (%s): %s",
                    slice_id, operation, reason)
    except Exception as exc:
        logger.error("[QM] No se pudo publicar el resultado ERROR de slice %s: %s",
                     slice_id, exc, exc_info=True)


async def handle_deploy(msg: Msg) -> None:
    """
    Handler para slice.deploy
    Publicado por el Slice Manager cuando quiere desplegar un slice.
    """
    heartbeat = asyncio.create_task(_keep_ack_alive(msg, settings.SUBJECT_DEPLOY))
    payload = None
    try:
        payload = json.loads(msg.data.decode())
        request = DeploySliceRequest(**payload)
        logger.info("="*70)
        logger.info("[QM] 📥 Mensaje NATS recibido en 'slice.deploy'")
        logger.info("[QM]    slice_id=%s  request_id=%s  VMs=%d  links=%d",
                    request.slice_id, request.request_id,
                    len(request.vms), len(request.links))
        await _orchestrator.deploy(request)
    except Exception as exc:
        logger.error("[QM] ❌ Error procesando slice.deploy: %s", exc, exc_info=True)
        await _publish_error_result(payload, "slice.deploy", str(exc))
    finally:
        heartbeat.cancel()
        await msg.ack()


async def handle_destroy(msg: Msg) -> None:
    """
    Handler para slice.destroy
    Publicado por el Slice Manager cuando quiere destruir un slice.
    """
    heartbeat = asyncio.create_task(_keep_ack_alive(msg, settings.SUBJECT_DESTROY))
    payload = None
    try:
        payload = json.loads(msg.data.decode())
        request = DestroySliceRequest(**payload)
        logger.info("[QM] 📥 Mensaje NATS recibido en 'slice.destroy'")
        logger.info("[QM]    slice_id=%s  request_id=%s", request.slice_id, request.request_id)
        await _orchestrator.destroy(request)
    except Exception as exc:
        logger.error("[QM] ❌ Error procesando slice.destroy: %s", exc, exc_info=True)
        await _publish_error_result(payload, "slice.destroy", str(exc))
    finally:
        heartbeat.cancel()
        await msg.ack()
