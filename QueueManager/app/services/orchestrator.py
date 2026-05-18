"""
Orquestador del Queue Manager.
Coordina los pasos de deploy/destroy entre módulos internos.

Pasos actuales:
    1. Compute Provisioner → levantar / destruir VMs

Pasos futuros (solo agregar aquí):
    2. Network Orchestrator → configurar / desconfigurar red
"""

import logging
from typing import List, Optional

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
        """
        Orquesta el despliegue de un slice.

        Pasos actuales:
            1. Compute Provisioner → levantar VMs
            2. Network Orchestrator → configurar red
            
        """
        logger.info(f"[slice={request.slice_id}] Iniciando deploy")

        state = OperationState(
            slice_id=request.slice_id,
            request_id=request.request_id,
            operation="deploy",
            vms=request.vms,
        )
        await self._save_state(state)

        # ── Paso 1: Compute ──────────────────────────────────────────────────
        logger.info(f"[slice={request.slice_id}] Paso 1: Solicitando aprovisionamiento de cómputo...")
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

        # Si no hay links de red (ej: 1 sola VM), saltar el paso de red
        if not request.links:
            logger.info(f"[slice={request.slice_id}] Sin links de red, se omite Network Orchestrator.")
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
            logger.info(f"[slice={request.slice_id}] Deploy finalizado (sin red): {final_status}")
            return response

        network_result = await self._step_network_deploy(request)

        if network_result is None:
            return await self._fail_deploy(state, "Timeout: Network Orchestrator no respondió")
            
        # NUEVO: Validar que el status de red no sea error
        if network_result.get("status") != "success":
            return await self._fail_deploy(state, "Error en Network Orchestrator al configurar VLANs")

        state.completed_steps.append(OperationStep.NETWORK)
        await self._save_state(state)
        logger.info(f"[slice={request.slice_id}] Network completado exitosamente")
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

# ── Destroy ───────────────────────────────────────────────────────────────

    async def destroy(self, request: DestroySliceRequest) -> DestroySliceResponse:
        """
        Orquesta la destrucción de un slice.

        Pasos actuales:
            0. Network Orchestrator → desconfigurar red (antes del compute)
            1. Compute Provisioner → destruir VMs
        """
        logger.info(f"[slice={request.slice_id}] Iniciando destroy")

        state = OperationState(
            slice_id=request.slice_id,
            request_id=request.request_id,
            operation="destroy",
        )
        await self._save_state(state)

        # ── Paso 0: Network (Limpieza de red primero) ────────────────────────
        logger.info(f"[slice={request.slice_id}] Paso 0: Limpiando red física...")
        network_result = await self._step_network_destroy(request)
        if not network_result:
             logger.warning(f"[slice={request.slice_id}] Network Orchestrator no respondió al destroy, continuando...")

        # ── Paso 1: Compute ──────────────────────────────────────────────────
        logger.info(f"[slice={request.slice_id}] Paso 1: Destruyendo VMs...")
        compute_result = await self._step_compute_destroy(request)

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
        """
        Envía el deploy al Compute Provisioner via core NATS request/reply.
        tap_interfaces viaja automáticamente dentro de cada VMSpec serializado.
        """
        payload = {
            "slice_id":   request.slice_id,
            "request_id": request.request_id,
            "vms":        [vm.model_dump() for vm in request.vms],
        }
        logger.debug(f"[slice={request.slice_id}] Enviando a {settings.SUBJECT_COMPUTE_DEPLOY}")
        return await nats_manager.request(
            settings.SUBJECT_COMPUTE_DEPLOY,
            payload,
            timeout=settings.COMPUTE_TIMEOUT,
        )

    async def _step_compute_destroy(self, request: DestroySliceRequest) -> Optional[dict]:
        """Envía el destroy al Compute Provisioner y espera respuesta."""
        payload = {
            "slice_id":   request.slice_id,
            "request_id": request.request_id,
            # 🔥 FIX: Empaquetamos la lista de VMs para que el compute sepa qué borrar
            "vms":        [vm.model_dump() for vm in request.vms], 
        }
        logger.debug(f"[slice={request.slice_id}] Enviando a {settings.SUBJECT_COMPUTE_DESTROY}")
        return await nats_manager.request(
            settings.SUBJECT_COMPUTE_DESTROY,
            payload,
            timeout=settings.COMPUTE_TIMEOUT,
        )

    async def _step_network_destroy(self, request: DestroySliceRequest) -> Optional[dict]:
        """Llama al Network Orchestrator para borrar puertos OVS, VLANs y Gateways"""
        payload = {
            "slice_id":   request.slice_id, 
            "request_id": request.request_id,
            # 🔥 FIX: Empaquetamos la lista de links para que OVS sepa qué TAPs desconectar
            "links":      [link.model_dump() for link in request.links], 
            # 🔥 FIX: Enviamos las VMs por si el Network Orchestrator necesita limpiar algo específico
            "vms":        [vm.model_dump() for vm in request.vms]
        }
        return await nats_manager.request(settings.SUBJECT_NETWORK_DESTROY, payload, timeout=settings.NETWORK_TIMEOUT)

    # ── Notificación al Slice Manager ─────────────────────────────────────────

    async def _notify_slice_manager(self, payload: dict) -> None:
        """Publica el resultado final en el subject que escucha el Slice Manager."""
        await nats_manager.publish(settings.SUBJECT_RESULT, payload)
        logger.debug(f"Resultado publicado en {settings.SUBJECT_RESULT}")

    # ── Helpers de estado ─────────────────────────────────────────────────────

    async def _save_state(self, state: OperationState) -> None:
        key = _STATE_KEY.format(slice_id=state.slice_id)
        await nats_manager.kv_put(key, state.model_dump())

    async def _delete_state(self, slice_id: str) -> None:
        key = _STATE_KEY.format(slice_id=slice_id)
        await nats_manager.kv_delete(key)

    async def _fail_deploy(
        self,
        state: OperationState,
        reason: str,
        failed_vms: Optional[List[VMResult]] = None,
    ) -> DeploySliceResponse:
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

    async def _fail_destroy(
        self,
        state: OperationState,
        reason: str,
    ) -> DestroySliceResponse:
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
    async def _step_network_deploy(self, request: DeploySliceRequest) -> Optional[dict]:
        """Llama al Network Orchestrator para configurar VLANs y OVS y el Gateway"""
        payload = {
            "slice_id":   request.slice_id,
            "request_id": request.request_id,
            "links":      [link.model_dump() for link in request.links],
            # 🔥 FIX: ¡Ahora enviamos también las VMs para que configure el NAT y DHCP!
            "vms":        [vm.model_dump() for vm in request.vms]
            
        }
        
        logger.info(f"[slice={request.slice_id}] Solicitando configuración de red...")
        # Cambiar el timeout si tus scripts de OVS son lentos (ej. 60s)
        return await nats_manager.request("network.deploy", payload, timeout=60)
