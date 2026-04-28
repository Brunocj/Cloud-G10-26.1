"""
Orquestador principal del Compute Provisioner.

Flujo de deploy por VM:
  1. Abrir SSH al worker
  2. Asignar puerto VNC
  3. Crear disco QCOW2
  4. Crear interfaces TAP y conectarlas a br-int (OVS)
  5. Lanzar QEMU con las TAPs y MACs
  6. Obtener PID

Flujo de destroy por VM:
  1. Matar proceso QEMU
  2. Eliminar TAP interfaces del OVS y del kernel
  3. Eliminar disco QCOW2
"""

import asyncio
import logging
from typing import List, Tuple

from app.core.config import settings
from app.models.schemas import (
    DeployReply, DeployRequest, DeployStatus,
    DestroyReply, DestroyRequest,
    VMResult, VMSpec,
)
from app.services.qemu_executor import QEMUExecutor
from app.services.queue_client import QueueClient
from app.services.ssh_client import SSHClient
from app.services.vnc_port_manager import VNCPortManager

logger = logging.getLogger(__name__)


class Provisioner:
    """Orquesta el ciclo de vida completo de las VMs."""

    def __init__(self):
        self._vnc = VNCPortManager()

    # ------------------------------------------------------------------
    # Deploy
    # ------------------------------------------------------------------

    async def deploy(self, request: DeployRequest) -> DeployReply:
        logger.info(
            "Deploy slice=%s, request=%s, vms=%d",
            request.slice_id, request.request_id, len(request.vms),
        )

        sem = asyncio.Semaphore(settings.MAX_CONCURRENT_WORKERS)
        tasks = [
            self._deploy_vm(vm, request.slice_id, sem)
            for vm in request.vms
        ]
        results: List[VMResult] = await asyncio.gather(*tasks)

        ok     = [r for r in results if r.error is None]
        failed = [r for r in results if r.error is not None]

        if not failed:
            status = DeployStatus.SUCCESS
        elif not ok:
            status = DeployStatus.ERROR
        else:
            status = DeployStatus.PARTIAL

        reply = DeployReply(
            slice_id=request.slice_id,
            request_id=request.request_id,
            status=status,
            vms=ok,
            failed_vms=failed,
        )

        # Persistir VMs desplegadas para poder destruirlas luego
        if ok:
            await QueueClient()._save_slice_vms(request.slice_id, request.vms)

        return reply

    async def _deploy_vm(
        self,
        vm: VMSpec,
        slice_id: str,
        sem: asyncio.Semaphore,
    ) -> VMResult:
        async with sem:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._deploy_vm_sync, vm, slice_id
            )

    def _deploy_vm_sync(self, vm: VMSpec, slice_id: str) -> VMResult:
        vnc_port: int | None = None

        for attempt in range(1, settings.SSH_MAX_RETRIES + 1):
            try:
                with SSHClient(vm.worker_ip, vm.ssh_user, vm.ssh_private_key) as ssh:
                    executor = QEMUExecutor(ssh)

                    # 1. Puerto VNC
                    vnc_display = self._vnc.acquire_port(vm.worker_ip, ssh)
                    vnc_port    = settings.VNC_PORT_MIN - 1 + vnc_display

                    # 2. Disco
                    disk_path = executor.create_disk(vm.vm_id, slice_id, vm.image_name)

                    # 3. TAP interfaces (si las hay)
                    if vm.tap_interfaces:
                        executor.create_tap_interfaces(vm.tap_interfaces)

                    # 4. Lanzar QEMU
                    pid = executor.launch_vm(
                        vm_id=vm.vm_id,
                        slice_id=slice_id,
                        disk_path=disk_path,
                        vcpus=vm.vcpus,
                        ram_mb=vm.ram_mb,
                        vnc_display=vnc_display,
                        tap_interfaces=vm.tap_interfaces,
                        priority=vm.priority or 0,
                    )

                    return VMResult(
                        vm_id=vm.vm_id,
                        worker_ip=vm.worker_ip,
                        pid=pid,
                        vnc_port=vnc_port,
                    )

            except Exception as exc:
                logger.warning(
                    "VM %s intento %d/%d falló: %s",
                    vm.vm_id, attempt, settings.SSH_MAX_RETRIES, exc,
                )
                if attempt == settings.SSH_MAX_RETRIES:
                    if vnc_port:
                        self._vnc.release_port(vm.worker_ip, vnc_port)
                    return VMResult(
                        vm_id=vm.vm_id,
                        worker_ip=vm.worker_ip,
                        error=str(exc),
                    )
                import time; time.sleep(settings.SSH_RETRY_DELAY)

    # ------------------------------------------------------------------
    # Destroy
    # ------------------------------------------------------------------

    async def destroy(self, request: DestroyRequest) -> DestroyReply:
        logger.info("Destroy slice=%s", request.slice_id)

        vms = await QueueClient()._load_deployed_vms(request.slice_id)
        if not vms:
            logger.warning("No hay VMs registradas para slice=%s", request.slice_id)
            return DestroyReply(
                slice_id=request.slice_id,
                request_id=request.request_id,
                status=DeployStatus.SUCCESS,
            )

        sem = asyncio.Semaphore(settings.MAX_CONCURRENT_WORKERS)
        tasks = [
            self._destroy_vm(vm, request.slice_id, sem)
            for vm in vms
        ]
        errors = await asyncio.gather(*tasks)
        errors = [e for e in errors if e]

        if errors:
            return DestroyReply(
                slice_id=request.slice_id,
                request_id=request.request_id,
                status=DeployStatus.ERROR,
                error="; ".join(errors),
            )

        await QueueClient()._delete_slice_vms(request.slice_id)
        return DestroyReply(
            slice_id=request.slice_id,
            request_id=request.request_id,
            status=DeployStatus.SUCCESS,
        )

    async def _destroy_vm(
        self,
        vm: VMSpec,
        slice_id: str,
        sem: asyncio.Semaphore,
    ):
        async with sem:
            return await asyncio.get_event_loop().run_in_executor(
                None, self._destroy_vm_sync, vm, slice_id
            )

    def _destroy_vm_sync(self, vm: VMSpec, slice_id: str):
        try:
            with SSHClient(vm.worker_ip, vm.ssh_user, vm.ssh_private_key) as ssh:
                executor = QEMUExecutor(ssh)

                # Orden: matar proceso → limpiar TAPs → eliminar disco
                executor.kill_vm(vm.vm_id, slice_id)

                if vm.tap_interfaces:
                    executor.destroy_tap_interfaces(vm.tap_interfaces)

                executor.delete_disk(vm.vm_id, slice_id)

            logger.info("VM %s destruida correctamente", vm.vm_id)
            return None

        except Exception as exc:
            logger.error("Error destruyendo VM %s: %s", vm.vm_id, exc)
            return str(exc)
