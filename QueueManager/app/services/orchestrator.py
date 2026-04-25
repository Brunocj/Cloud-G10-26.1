"""
Orquestador de operaciones de slice.
"""

import logging
from typing import Optional

from app.core.config import settings
from app.models.schemas import (
    DeploySliceRequest, DeploySliceResponse,
    DestroySliceRequest, DestroySliceResponse,
    OperationState, OperationStep,
    SliceStatus, VMResult,
)
from app.services.nats_client import nats_manager

logger = logging.getLogger(__name__)

_STATE_KEY = "op:{slice_id}"


class Orchestrator:

    # ── Deploy ────────────────────────────────────────────────────────────────

    async def deploy(self, request: DeploySliceRequest) -> DeploySliceResponse:
        logger.info(f"[slice={request.slice_id}] Iniciando deploy")

        state = OperationState(
            slice_id=request.slice_id,
            request_id=request.request_id,
            operation="deploy",
            vms=request.vms,
        )
        await self._save_state(state)

        # ── Paso 1: Compute ──────────────────────────────────────────────────
        compute_result = await self._step_compute_deploy(request)
        logger.info(f"[slice={request.slice_id}] Respuesta compute: {compute_result}")

        if compute_result is None:
            return await self._fail_deploy(
                state, "Timeout: Compute Provisioner no respondió"
            )

        successful = [VMResult(**vm) for vm in compute_result.get("vms", [])]
        failed     = [VMResult(**vm) for vm in compute_result.get("failed_vms", [])]
        status     = compute_result.get("status", "error")

        # Aceptar success y partial como válidos
        if status == "error":
            return await self._fail_deploy(
                state,
                "Compute Provisioner reportó error en todas las VMs",
                failed_vms=failed,
            )

        state.completed_steps.append(OperationStep.COMPUTE)
        state.vm_results = successful
        await self._save_state(state)

        logger.info(
            f"[slice={request.slice_id}] Paso COMPUTE completado: "
            f"{len(successful)} VMs levantadas"
        )

        # ── Paso 2: Network (futuro) ─────────────────────────────────────────
        # TODO: descomentar cuando el Network Orchestrator esté implementado
        # network_result = await self._step_network_deploy(request, successful)
        # state.completed_steps.append(OperationStep.NETWORK)
        # await self._save_state(state)

        # ── Resultado final ──────────────────────────────────────────────────
        await self._delete_state(state.slice_id)

        final_status = SliceStatus(status)
        response = DeploySliceResponse(
            slice_id=request.slice_id,
            request_id=request.request_id,
            status=final_status,
            vms=successful,
            failed_vms=failed,
        )

        await self._notify_slice_manager(response.model_dump())
        logger.info(f"[slice={request.slice_id}] Deploy finalizado: {final_status}")
        return response

    # ── Destroy ───────────────────────────────────────────────────────────────

    async def destroy(self, request: DestroySliceRequest) -> DestroySliceResponse:
        logger.info(f"[slice={request.slice_id}] Iniciando destroy")

        state = OperationState(
            slice_id=request.slice_id,
            request_id=request.request_id,
            operation="destroy",
        )
        await self._save_state(state)

        compute_result = await self._step_compute_destroy(request)
        logger.info(f"[slice={request.slice_id}] Respuesta compute destroy: {compute_result}")

        if compute_result is None:
            return await self._fail_destroy(
                state, "Timeout: Compute Provisioner no respondió"
            )

        destroyed = compute_result.get("destroyed_vms", [])
        failed    = compute_result.get("failed_vms", [])
        status    = compute_result.get("status", "error")

        await self._delete_state(state.slice_id)

        response = DestroySliceResponse(
            slice_id=request.slice_id,
            request_id=request.request_id,
            status=SliceStatus(status),
            destroyed_vms=destroyed,
            failed_vms=failed,
        )

        await self._notify_slice_manager(response.model_dump())
        logger.info(f"[slice={request.slice_id}] Destroy finalizado: {status}")
        return response

    # ── Pasos internos ────────────────────────────────────────────────────────

    async def _step_compute_deploy(self, request: DeploySliceRequest) -> Optional[dict]:
        payload = {
            "slice_id":   request.slice_id,
            "request_id": request.request_id,
            "vms":        [vm.model_dump() for vm in request.vms],
        }
        logger.info(f"[slice={request.slice_id}] Enviando request a '{settings.SUBJECT_COMPUTE_DEPLOY}'")
        return await nats_manager.request(
            settings.SUBJECT_COMPUTE_DEPLOY,
            payload,
            timeout=settings.COMPUTE_TIMEOUT,
        )

    async def _step_compute_destroy(self, request: DestroySliceRequest) -> Optional[dict]:
        payload = {
            "slice_id":   request.slice_id,
            "request_id": request.request_id,
        }
        logger.info(f"[slice={request.slice_id}] Enviando request a '{settings.SUBJECT_COMPUTE_DESTROY}'")
        return await nats_manager.request(
            settings.SUBJECT_COMPUTE_DESTROY,
            payload,
            timeout=settings.COMPUTE_TIMEOUT,
        )

    # ── Notificación al Slice Manager ─────────────────────────────────────────

    async def _notify_slice_manager(self, payload: dict) -> None:
        await nats_manager.publish(settings.SUBJECT_RESULT, payload)
        logger.info(f"Resultado publicado en '{settings.SUBJECT_RESULT}'")

    # ── Manejo de errores ─────────────────────────────────────────────────────

    async def _fail_deploy(self, state: OperationState, reason: str,
                           failed_vms=None) -> DeploySliceResponse:
        logger.error(f"[slice={state.slice_id}] Deploy fallido: {reason}")
        await self._delete_state(state.slice_id)
        response = DeploySliceResponse(
            slice_id=state.slice_id,
            request_id=state.request_id,
            status=SliceStatus.ERROR,
            failed_vms=failed_vms or [],
        )
        await self._notify_slice_manager(response.model_dump())
        return response

    async def _fail_destroy(self, state: OperationState,
                            reason: str) -> DestroySliceResponse:
        logger.error(f"[slice={state.slice_id}] Destroy fallido: {reason}")
        await self._delete_state(state.slice_id)
        response = DestroySliceResponse(
            slice_id=state.slice_id,
            request_id=state.request_id,
            status=SliceStatus.ERROR,
            error=reason,
        )
        await self._notify_slice_manager(response.model_dump())
        return response

    # ── Estado en KV ──────────────────────────────────────────────────────────

    async def _save_state(self, state: OperationState) -> None:
        key = _STATE_KEY.format(slice_id=state.slice_id)
        await nats_manager.kv_put(key, state.model_dump())

    async def _delete_state(self, slice_id: str) -> None:
        key = _STATE_KEY.format(slice_id=slice_id)
        await nats_manager.kv_delete(key)
