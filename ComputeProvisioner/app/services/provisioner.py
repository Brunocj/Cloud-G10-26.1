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

import logging
import time
from typing import List, Optional

from app.core.config import settings
from app.models.schemas import (
    DeployReply, DeployRequest, DeployStatus,
    DestroyReply, DestroyRequest,
    VMResult, VMSpec,
)
from app.services.qemu_executor import QEMUExecutor
from app.services.ssh_client import SSHClient
from app.services.vnc_port_manager import VNCPortManager

logger = logging.getLogger(__name__)


class Provisioner:
    """Orquesta el ciclo de vida completo de las VMs."""

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # Deploy
    # ------------------------------------------------------------------

    def deploy(self, request: DeployRequest) -> DeployReply:
        logger.info(
            "Deploy slice=%s, request=%s, vms=%d",
            request.slice_id, request.request_id, len(request.vms),
        )

        results: List[VMResult] = [
            self._deploy_vm_sync(vm, request.slice_id)
            for vm in request.vms
        ]

        ok     = [r for r in results if r.error is None]
        failed = [r for r in results if r.error is not None]

        if not failed:
            status = DeployStatus.SUCCESS
        elif not ok:
            status = DeployStatus.ERROR
        else:
            status = DeployStatus.PARTIAL

        return DeployReply(
            slice_id=request.slice_id,
            request_id=request.request_id,
            status=status,
            vms=ok,
            failed_vms=failed,
        )

    def _deploy_vm_sync(self, vm: VMSpec, slice_id: str) -> VMResult:
        # 🔥 Leemos el puerto y calculamos el display aquí mismo
        vnc_port = vm.vnc_port
        vnc_display = vnc_port - 5900

        for attempt in range(1, settings.SSH_MAX_RETRIES + 1):
            try:
                with SSHClient(vm.worker_ip, vm.ssh_user, vm.ssh_private_key) as ssh:
                    executor = QEMUExecutor(ssh)

                    # 1. Disco
                    # 🔥 FIX: Le pasamos vm.disk_gb a la función
                    disk_path = executor.create_disk(vm.vm_id, slice_id, vm.image_path, vm.worker_ip, vm.disk_gb)

                    # 2. TAP interfaces
                    if vm.tap_interfaces:
                        executor.create_tap_interfaces(vm.tap_interfaces)

                    # 3. Lanzar QEMU con el display inyectado
                    pid = executor.launch_vm(
                        vm_id=vm.vm_id,
                        slice_id=slice_id,
                        disk_path=disk_path,
                        vcpus=vm.vcpus,
                        ram_mb=vm.ram_mb,
                        vnc_display=vnc_display, # 🔥 Usamos la variable local
                        tap_interfaces=vm.tap_interfaces,
                        priority=vm.priority,
                    )

                    return VMResult(
                        vm_id=vm.vm_id,
                        worker_ip=vm.worker_ip,
                        pid=pid,
                        vnc_port=vnc_port, # 🔥 Devolvemos el mismo puerto
                    )

            except Exception as exc:
                logger.warning(
                    "VM %s intento %d/%d falló: %s",
                    vm.vm_id, attempt, settings.SSH_MAX_RETRIES, exc,
                )
                if attempt == settings.SSH_MAX_RETRIES:
                    # 🔥 FIX: Eliminamos el if vnc_port: self._vnc.release_port(...)
                    return VMResult(
                        vm_id=vm.vm_id,
                        worker_ip=vm.worker_ip,
                        error=str(exc),
                    )
                time.sleep(settings.SSH_RETRY_DELAY)

    # ------------------------------------------------------------------
    # Destroy
    # ------------------------------------------------------------------

    def destroy(self, request: DestroyRequest, vm_records: List[dict]) -> DestroyReply:
        """
        Destruye las VMs de un slice.
        vm_records: lista de dicts con vm_id, worker_ip, ssh_user, ssh_private_key,
                    y tap_interfaces (si aplica). Viene del KV via worker.py.
        """
        logger.info("Destroy slice=%s, vms=%d", request.slice_id, len(vm_records))

        if not vm_records:
            logger.warning("No hay VMs registradas para slice=%s", request.slice_id)
            return DestroyReply(
                slice_id=request.slice_id,
                request_id=request.request_id,
                status=DeployStatus.SUCCESS,
            )

        errors = [
            self._destroy_vm_sync(record, request.slice_id)
            for record in vm_records
        ]
        errors = [e for e in errors if e]

        if errors:
            return DestroyReply(
                slice_id=request.slice_id,
                request_id=request.request_id,
                status=DeployStatus.ERROR,
                error="; ".join(errors),
            )

        return DestroyReply(
            slice_id=request.slice_id,
            request_id=request.request_id,
            status=DeployStatus.SUCCESS,
        )

    def _destroy_vm_sync(self, record: dict, slice_id: str) -> Optional[str]:
        vm_id      = record["vm_id"]
        worker_ip  = record["worker_ip"]
        ssh_user   = record["ssh_user"]
        ssh_key    = record["ssh_private_key"]
        tap_ifaces = record.get("tap_interfaces", [])

        try:
            with SSHClient(worker_ip, ssh_user, ssh_key) as ssh:
                executor = QEMUExecutor(ssh)

                executor.kill_vm(vm_id, slice_id)

                if tap_ifaces:
                    from app.models.schemas import TapInterface
                    taps = [TapInterface(**t) for t in tap_ifaces]
                    executor.destroy_tap_interfaces(taps)

                executor.delete_disk(vm_id, slice_id)

            logger.info("VM %s destruida correctamente", vm_id)
            return None

        except Exception as exc:
            logger.error("Error destruyendo VM %s: %s", vm_id, exc)
            return str(exc)
