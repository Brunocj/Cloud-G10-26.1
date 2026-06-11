"""
WorkflowOrchestrator — PUCP Private Cloud Orchestrator
=======================================================

Antiguo nombre: QueueManager / Orchestrator.
Nueva responsabilidad: Saga Pattern agnóstico a la plataforma (Linux Cluster u OpenStack).

Flujo de despliegue (ESTRICTO Y SECUENCIAL):
    Paso 1 — PLACEMENT : slice.placement.process  → VMPlacement (BYOS)
    Paso 2 — NETWORK   : slice.network.deploy     → NetworkOrchestrator
    Paso 3 — COMPUTE   : slice.compute.deploy     → ComputeProvisioner
    Paso 4 — STATE     : slice.state.update       → SliceManager (marca ACTIVE)

Rollback (orden inverso al fallo):
    Si falla COMPUTE  → destroy COMPUTE  → destroy NETWORK
    Si falla NETWORK  → destroy NETWORK
    Si falla PLACEMENT → no hay infraestructura que limpiar → notificar ERROR

Regla: el campo availability_zone_id viaja en TODOS los payloads hacia
       los módulos internos. Ningún módulo toma decisiones por nombre de zona.
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

# ID de AZ que corresponde a OpenStack (por convención)
AZ_ID_OPENSTACK   = int(__import__("os").getenv("OPENSTACK_AZ_ID", "2"))
AZ_ID_LINUX       = int(__import__("os").getenv("LINUX_AZ_ID",     "1"))


class WorkflowOrchestrator:
    """
    Orquestador de flujos de trabajo (Saga Pattern).

    Es completamente ciego a las plataformas subyacentes:
    recibe availability_zone_id y lo propaga en cada evento NATS sin
    interpretar si es Linux Cluster u OpenStack.
    """

    # ══════════════════════════════════════════════════════════════════════════
    # DEPLOY — Saga de 4 pasos
    # ══════════════════════════════════════════════════════════════════════════

    async def deploy(self, request: DeploySliceRequest) -> DeploySliceResponse:
        """
        Orquesta el despliegue de un slice siguiendo la nueva Saga.

        Orden ESTRICTO: Placement → Network → Compute → StateUpdate
        """
        slice_id = request.slice_id
        az_id    = request.availability_zone_id

        logger.info("=" * 70)
        logger.info("[SAGA][%s] Iniciando deploy — az_id=%d", slice_id, az_id)

        state = OperationState(
            slice_id=slice_id,
            request_id=request.request_id,
            operation="deploy",
            vms=request.vms,
        )
        await self._save_state(state)

        # ── Paso 1: PLACEMENT ─────────────────────────────────────────────────
        logger.info("[SAGA][%s] Paso 1/4 — PLACEMENT (slice.placement.process) …", slice_id)
        placement_result = await self._step_placement(request)

        if placement_result is None:
            return await self._fail_deploy(
                state, "Timeout: VMPlacement no respondió en el tiempo esperado."
            )
        if placement_result.get("status") != "SUCCESS":
            reason = placement_result.get("reason", "UNKNOWN")
            detail = placement_result.get("detail", "")
            return await self._fail_deploy(
                state, f"Placement FAILED — {reason}: {detail}"
            )

        placement_map: list = placement_result.get("placement_map", [])
        # Mapa: {vm_id → selected_host} para enriquecer los payloads siguientes
        host_map: dict[str, str] = {
            entry["vm_id"]: entry.get("selected_host", str(entry.get("worker_id", "")))
            for entry in placement_map
        }
        logger.info(
            "[SAGA][%s] Paso 1 COMPLETADO — %d VMs asignadas a hosts físicos: %s",
            slice_id, len(host_map), host_map,
        )
        state.completed_steps.append(OperationStep.PLACEMENT)
        await self._save_state(state)

        # ── Paso 2: NETWORK ───────────────────────────────────────────────────
        # La red siempre se crea ANTES que el cómputo en la nueva arquitectura.
        if request.links:
            logger.info("[SAGA][%s] Paso 2/4 — NETWORK (network.deploy) …", slice_id)
            network_result = await self._step_network_deploy(request, host_map, az_id)

            if network_result is None:
                return await self._fail_deploy(
                    state, "Timeout: Network Orchestrator no respondió."
                )
            if network_result.get("status") != "success":
                err = network_result.get("error", "desconocido")
                return await self._fail_deploy(
                    state, f"Network Orchestrator reportó error: {err}"
                )

            # Capturar UUIDs de puertos lógicos creados (clave para OpenStack Neutron)
            port_map: dict = network_result.get("port_map", {})
            logger.info(
                "[SAGA][%s] Paso 2 COMPLETADO — %d puertos creados", slice_id, len(port_map)
            )
            state.completed_steps.append(OperationStep.NETWORK)
            await self._save_state(state)
        else:
            logger.info("[SAGA][%s] Sin links — Paso 2 (NETWORK) omitido.", slice_id)
            port_map = {}

        # ── Paso 3: COMPUTE ───────────────────────────────────────────────────
        logger.info("[SAGA][%s] Paso 3/4 — COMPUTE (compute.deploy) …", slice_id)
        compute_result = await self._step_compute_deploy(request, host_map, port_map, az_id)

        if compute_result is None:
            # Rollback: destruir la red que ya se creó
            await self._rollback_network(request, az_id)
            return await self._fail_deploy(
                state, "Timeout: Compute Provisioner no respondió."
            )

        successful = [VMResult(**vm) for vm in compute_result.get("vms", [])]
        failed     = [VMResult(**vm) for vm in compute_result.get("failed_vms", [])]
        status_str = compute_result.get("status", "error")

        if status_str == "error":
            await self._rollback_network(request, az_id)
            return await self._fail_deploy(
                state,
                "Compute Provisioner reportó error en todas las VMs.",
                failed_vms=failed,
            )

        logger.info(
            "[SAGA][%s] Paso 3 COMPLETADO — %d VMs levantadas, %d fallidas",
            slice_id, len(successful), len(failed),
        )
        state.completed_steps.append(OperationStep.COMPUTE)
        state.vm_results = successful
        await self._save_state(state)

        # ── Paso 4: STATE UPDATE ──────────────────────────────────────────────
        logger.info("[SAGA][%s] Paso 4/4 — STATE UPDATE (slice.state.update) …", slice_id)
        await self._step_state_update(
            slice_id=slice_id,
            request_id=request.request_id,
            az_id=az_id,
            vm_results=successful,
            failed_vms=failed,
            final_status=status_str,
        )
        state.completed_steps.append(OperationStep.STATE)
        await self._delete_state(slice_id)

        # ── Resultado final ───────────────────────────────────────────────────
        final_status = SliceStatus(status_str)
        response = DeploySliceResponse(
            slice_id=slice_id,
            request_id=request.request_id,
            status=final_status,
            vms=successful,
            failed_vms=failed,
        )
        await self._notify_slice_manager(response.model_dump())
        logger.info("[SAGA][%s] Deploy finalizado: %s", slice_id, final_status)
        logger.info("=" * 70)
        return response

    # ══════════════════════════════════════════════════════════════════════════
    # DESTROY — Saga inversa (Network → Compute)
    # ══════════════════════════════════════════════════════════════════════════

    async def destroy(self, request: DestroySliceRequest) -> DestroySliceResponse:
        """
        Orquesta la destrucción de un slice (orden inverso al deploy).

        Pasos:
            0. Network destroy  (VLANs, TAPs, OVS, puertos Neutron)
            1. Compute destroy  (terminar instancias)
        """
        slice_id = request.slice_id
        az_id    = getattr(request, "availability_zone_id", AZ_ID_LINUX)

        logger.info("[SAGA][%s] Iniciando destroy — az_id=%d", slice_id, az_id)

        state = OperationState(
            slice_id=slice_id,
            request_id=request.request_id,
            operation="destroy",
        )
        await self._save_state(state)

        # ── Paso 0: Network (siempre primero en destroy) ──────────────────────
        logger.info("[SAGA][%s] Paso 0: Limpiando red física …", slice_id)
        network_result = await self._step_network_destroy(request, az_id)
        if not network_result:
            logger.warning(
                "[SAGA][%s] Network no respondió al destroy — continuando igualmente.", slice_id
            )

        # ── Paso 1: Compute ───────────────────────────────────────────────────
        logger.info("[SAGA][%s] Paso 1: Destruyendo instancias/VMs …", slice_id)
        compute_result = await self._step_compute_destroy(request, az_id)

        if compute_result is None:
            return await self._fail_destroy(
                state, "Timeout: Compute Provisioner no respondió al destroy."
            )

        destroyed = compute_result.get("destroyed_vms", [])
        failed    = compute_result.get("failed_vms", [])
        status    = compute_result.get("status", "error")

        await self._delete_state(slice_id)

        response = DestroySliceResponse(
            slice_id=slice_id,
            request_id=request.request_id,
            status=SliceStatus(status),
            destroyed_vms=destroyed,
            failed_vms=failed,
        )
        await self._notify_slice_manager(response.model_dump())
        logger.info("[SAGA][%s] Destroy finalizado: %s", slice_id, status)
        return response

    # ══════════════════════════════════════════════════════════════════════════
    # Pasos internos — Deploy
    # ══════════════════════════════════════════════════════════════════════════

    async def _step_placement(self, request: DeploySliceRequest) -> Optional[dict]:
        """
        Paso 1: Solicita al VMPlacement (BYOS) la asignación física de VMs.

        El VMPlacement recibe availability_zone_id y aplica el Strategy:
          - Linux Cluster → consulta workers locales + CP-SAT
          - OpenStack     → consulta Nova Hypervisors API + CP-SAT

        El payload de respuesta incluye `selected_host` por cada VM.
        """
        payload = {
            "slice_id":             request.slice_id,
            "request_id":           request.request_id,
            "availability_zone_id": request.availability_zone_id,
            "vms": [
                {
                    "vm_id":    vm.vm_id,
                    "vcpus":    vm.vcpus,
                    "ram_gb":   round(vm.ram_mb / 1024.0, 4),
                    "disco_gb": vm.disk_gb,
                }
                for vm in request.vms
            ],
        }
        logger.debug("[SAGA][%s] → %s payload=%s",
                     request.slice_id, settings.SUBJECT_PLACEMENT, payload)
        return await nats_manager.request(
            settings.SUBJECT_PLACEMENT,
            payload,
            timeout=settings.PLACEMENT_TIMEOUT,
        )

    async def _step_network_deploy(
        self,
        request: DeploySliceRequest,
        host_map: dict,
        az_id: int,
    ) -> Optional[dict]:
        """
        Paso 2: Configura la red ANTES que el cómputo.

        Inyecta host_map y availability_zone_id para que el NetworkOrchestrator
        sepa dónde crear puertos (OVS en Linux / Neutron en OpenStack).
        """
        payload = {
            "slice_id":             request.slice_id,
            "request_id":           request.request_id,
            "availability_zone_id": az_id,
            "host_map":             host_map,           # vm_id → selected_host
            "links":  [link.model_dump() for link in request.links],
            "vms":    [vm.model_dump()   for vm   in request.vms],
        }
        logger.info("[SAGA][%s] → %s", request.slice_id, settings.SUBJECT_NETWORK_DEPLOY)
        return await nats_manager.request(
            settings.SUBJECT_NETWORK_DEPLOY,
            payload,
            timeout=settings.NETWORK_TIMEOUT,
        )

    async def _step_compute_deploy(
        self,
        request: DeploySliceRequest,
        host_map: dict,
        port_map: dict,
        az_id: int,
    ) -> Optional[dict]:
        """
        Paso 3: Levanta las instancias/VMs.

        Inyecta:
          - host_map  → selected_host por VM (resultado del placement)
          - port_map  → UUIDs de puertos lógicos creados por la red
          - availability_zone_id → para que compute sepa si usar QEMU/KVM o Nova API
        """
        vms_payload = []
        for vm in request.vms:
            vm_dict = vm.model_dump()
            vm_dict["selected_host"] = host_map.get(vm.vm_id, "")
            vm_dict["network_ports"] = port_map.get(vm.vm_id, [])
            vms_payload.append(vm_dict)

        payload = {
            "slice_id":             request.slice_id,
            "request_id":           request.request_id,
            "availability_zone_id": az_id,
            "vms":                  vms_payload,
        }
        logger.info("[SAGA][%s] → %s (%d VMs)",
                    request.slice_id, settings.SUBJECT_COMPUTE_DEPLOY, len(vms_payload))
        return await nats_manager.request(
            settings.SUBJECT_COMPUTE_DEPLOY,
            payload,
            timeout=settings.COMPUTE_TIMEOUT,
        )

    async def _step_state_update(
        self,
        slice_id: str,
        request_id: str,
        az_id: int,
        vm_results: List[VMResult],
        failed_vms: List[VMResult],
        final_status: str,
    ) -> None:
        """
        Paso 4: Publica slice.state.update para que el Slice Manager
        marque el slice como ACTIVE (o PARTIAL) en la base de datos central.

        Incluye vnc_url por cada VM para el caso OpenStack.
        """
        payload = {
            "slice_id":             slice_id,
            "request_id":           request_id,
            "availability_zone_id": az_id,
            "status":               final_status,
            "vms": [
                {
                    "vm_id":    vm.vm_id,
                    "vnc_port": vm.vnc_port,
                    "vnc_url":  getattr(vm, "vnc_url", None),
                    "worker_ip": vm.worker_ip,
                    "error":    vm.error,
                }
                for vm in vm_results
            ],
            "failed_vms": [vm.vm_id for vm in failed_vms],
        }
        logger.info("[SAGA][%s] → %s  status=%s", slice_id, settings.SUBJECT_STATE_UPDATE, final_status)
        await nats_manager.publish(settings.SUBJECT_STATE_UPDATE, payload)

    # ══════════════════════════════════════════════════════════════════════════
    # Pasos internos — Destroy
    # ══════════════════════════════════════════════════════════════════════════

    async def _step_compute_destroy(
        self, request: DestroySliceRequest, az_id: int
    ) -> Optional[dict]:
        """Envía el destroy al Compute Provisioner."""
        payload = {
            "slice_id":             request.slice_id,
            "request_id":           request.request_id,
            "availability_zone_id": az_id,
            "vms":   [vm.model_dump()   for vm   in request.vms],
        }
        logger.debug("[SAGA][%s] → %s", request.slice_id, settings.SUBJECT_COMPUTE_DESTROY)
        return await nats_manager.request(
            settings.SUBJECT_COMPUTE_DESTROY,
            payload,
            timeout=settings.COMPUTE_TIMEOUT,
        )

    async def _step_network_destroy(
        self, request: DestroySliceRequest, az_id: int
    ) -> Optional[dict]:
        """Llama al Network Orchestrator para borrar puertos, VLANs y Gateways."""
        payload = {
            "slice_id":             request.slice_id,
            "request_id":           request.request_id,
            "availability_zone_id": az_id,
            "links": [link.model_dump() for link in request.links],
            "vms":   [vm.model_dump()   for vm   in request.vms],
        }
        return await nats_manager.request(
            settings.SUBJECT_NETWORK_DESTROY,
            payload,
            timeout=settings.NETWORK_TIMEOUT,
        )

    # ══════════════════════════════════════════════════════════════════════════
    # Rollback helpers
    # ══════════════════════════════════════════════════════════════════════════

    async def _rollback_network(self, request: DeploySliceRequest, az_id: int) -> None:
        """
        Rollback del paso NETWORK: intenta limpiar la red creada.
        Se llama cuando el Compute falla después de que la red ya se configuró.
        No lanza excepción aunque falle — sólo loguea.
        """
        if not request.links:
            return
        logger.warning(
            "[SAGA][%s] Rollback: destruyendo red creada …", request.slice_id
        )
        try:
            destroy_req = DestroySliceRequest(
                slice_id=request.slice_id,
                request_id=request.request_id,
                vms=request.vms,
                links=request.links,
            )
            result = await self._step_network_destroy(destroy_req, az_id)
            if result:
                logger.info("[SAGA][%s] Rollback de red: OK", request.slice_id)
            else:
                logger.warning(
                    "[SAGA][%s] Rollback de red: timeout — recursos pueden quedar huérfanos.",
                    request.slice_id,
                )
        except Exception as exc:
            logger.error(
                "[SAGA][%s] Rollback de red falló con excepción: %s",
                request.slice_id, exc,
            )

    # ══════════════════════════════════════════════════════════════════════════
    # Notificaciones y estado
    # ══════════════════════════════════════════════════════════════════════════

    async def _notify_slice_manager(self, payload: dict) -> None:
        """Publica el resultado final en el subject que escucha el Slice Manager."""
        await nats_manager.publish(settings.SUBJECT_RESULT, payload)
        logger.debug("Resultado publicado en %s", settings.SUBJECT_RESULT)

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
        logger.error("[SAGA][%s] Deploy fallido: %s", state.slice_id, reason)
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
        logger.error("[SAGA][%s] Destroy fallido: %s", state.slice_id, reason)
        await self._delete_state(state.slice_id)
        response = DestroySliceResponse(
            slice_id=state.slice_id,
            request_id=state.request_id,
            status=SliceStatus.ERROR,
            error=reason,
        )
        await self._notify_slice_manager(response.model_dump())
        return response


# Alias de compatibilidad: el resto del código (handlers.py) importa Orchestrator
Orchestrator = WorkflowOrchestrator
