"""
Servicio principal del Compute Provisioner.
Responsabilidad estricta: levantar y destruir VMs en los workers via SSH+QEMU.
No configura red, no crea TAPs, no gestiona VLANs.
"""

import logging
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Tuple

from app.core.config import settings
from app.models.schemas import (
    DeploySliceRequest, DeploySliceResponse,
    DestroySliceRequest, DestroySliceResponse,
    ProvisioningStatus, VMResult, VMSpec,
)
from app.services.qemu_executor import QEMUExecutor
from app.services.ssh_client import SSHClient
from app.services.vnc_port_manager import VNCPortManager

logger = logging.getLogger(__name__)


class ComputeProvisioner:

    # ── Deploy ───────────────────────────────────────────────────────────────

    def deploy(self, request: DeploySliceRequest) -> DeploySliceResponse:
        logger.info(f"[slice={request.slice_id}] Iniciando despliegue de {len(request.vms)} VMs")

        vms_by_worker: Dict[str, List[VMSpec]] = defaultdict(list)
        for vm in request.vms:
            vms_by_worker[vm.worker_ip].append(vm)

        successful: List[VMResult] = []
        failed:     List[VMResult] = []

        with ThreadPoolExecutor(max_workers=settings.MAX_CONCURRENT_WORKERS) as pool:
            futures = {
                pool.submit(self._deploy_on_worker, worker_ip, vms, request.slice_id): worker_ip
                for worker_ip, vms in vms_by_worker.items()
            }
            for future in as_completed(futures):
                worker_ip = futures[future]
                try:
                    ok, fail = future.result()
                    successful.extend(ok)
                    failed.extend(fail)
                except Exception as exc:
                    logger.error(f"[worker={worker_ip}] Error inesperado: {exc}")
                    for vm in vms_by_worker[worker_ip]:
                        failed.append(VMResult(vm_id=vm.vm_id, worker_ip=worker_ip, error=str(exc)))

        status = self._compute_status(successful, failed, len(request.vms))
        logger.info(f"[slice={request.slice_id}] Despliegue finalizado: status={status}")

        return DeploySliceResponse(
            slice_id=request.slice_id,
            request_id=request.request_id,
            status=status,
            vms=successful,
            failed_vms=failed,
        )

    def _deploy_on_worker(self, worker_ip: str, vms: List[VMSpec],
                          slice_id: str) -> Tuple[List[VMResult], List[VMResult]]:
        successful: List[VMResult] = []
        failed:     List[VMResult] = []
        executor = QEMUExecutor(worker_ip)
        vnc_mgr  = VNCPortManager()

        try:
            with SSHClient(worker_ip, vms[0].ssh_user, vms[0].ssh_private_key) as ssh:
                for vm in vms:
                    result = self._deploy_single_vm_with_retry(ssh, executor, vnc_mgr, vm, slice_id)
                    if result.error:
                        failed.append(result)
                    else:
                        successful.append(result)
        except Exception as exc:
            logger.error(f"[worker={worker_ip}] Fallo SSH: {exc}")
            for vm in vms:
                failed.append(VMResult(vm_id=vm.vm_id, worker_ip=worker_ip,
                                       error=f"SSH connection failed: {exc}"))

        return successful, failed

    def _deploy_single_vm_with_retry(self, ssh, executor, vnc_mgr, vm, slice_id) -> VMResult:
        try:
            vnc_port = vnc_mgr.assign_port(vm.worker_ip, vm.ssh_user, vm.ssh_private_key)
        except RuntimeError as exc:
            return VMResult(vm_id=vm.vm_id, worker_ip=vm.worker_ip, error=str(exc))

        last_error = ""
        for attempt in range(1, settings.SSH_MAX_RETRIES + 1):
            try:
                logger.info(f"[worker={vm.worker_ip}] Desplegando {vm.vm_id} (intento {attempt})")
                disk_path = executor.create_thin_disk(ssh, vm.vm_id, slice_id, vm.image_name)
                pid       = executor.launch_vm(ssh, vm.vm_id, slice_id, disk_path,
                                               vm.vcpus, vm.ram_mb, vnc_port, vm.priority or 0)
                return VMResult(vm_id=vm.vm_id, worker_ip=vm.worker_ip, pid=pid, vnc_port=vnc_port)
            except Exception as exc:
                last_error = str(exc)
                logger.warning(f"[worker={vm.worker_ip}] Intento {attempt} falló: {exc}")
                try:
                    executor.delete_disk(ssh, vm.vm_id, slice_id)
                except Exception:
                    pass
                if attempt < settings.SSH_MAX_RETRIES:
                    time.sleep(settings.SSH_RETRY_DELAY)

        vnc_mgr.release_port(vm.worker_ip, vnc_port)
        return VMResult(vm_id=vm.vm_id, worker_ip=vm.worker_ip, error=last_error)

    # ── Destroy ───────────────────────────────────────────────────────────────

    def destroy(self, request: DestroySliceRequest, vm_records: list = None) -> DestroySliceResponse:
        logger.info(f"[slice={request.slice_id}] Iniciando destrucción")

        vm_records = vm_records or []

        if not vm_records:
            logger.warning(f"[slice={request.slice_id}] No hay VMs registradas")
            return DestroySliceResponse(
                slice_id=request.slice_id,
                request_id=request.request_id,
                status=ProvisioningStatus.SUCCESS,
            )

        vms_by_worker: Dict[str, List[dict]] = defaultdict(list)
        for record in vm_records:
            vms_by_worker[record["worker_ip"]].append(record)

        destroyed: List[str] = []
        failed:    List[str] = []

        with ThreadPoolExecutor(max_workers=settings.MAX_CONCURRENT_WORKERS) as pool:
            futures = {
                pool.submit(self._destroy_on_worker, worker_ip, records, request.slice_id): worker_ip
                for worker_ip, records in vms_by_worker.items()
            }
            for future in as_completed(futures):
                worker_ip = futures[future]
                try:
                    ok, fail = future.result()
                    destroyed.extend(ok)
                    failed.extend(fail)
                except Exception as exc:
                    logger.error(f"[worker={worker_ip}] Error en destrucción: {exc}")
                    for record in vms_by_worker[worker_ip]:
                        failed.append(record["vm_id"])

        status = (
            ProvisioningStatus.SUCCESS if not failed
            else ProvisioningStatus.ERROR if not destroyed
            else ProvisioningStatus.PARTIAL
        )

        return DestroySliceResponse(
            slice_id=request.slice_id,
            request_id=request.request_id,
            status=status,
            destroyed_vms=destroyed,
            failed_vms=failed,
        )

    def _destroy_on_worker(self, worker_ip: str, records: List[dict],
                           slice_id: str) -> Tuple[List[str], List[str]]:
        destroyed: List[str] = []
        failed:    List[str] = []
        executor = QEMUExecutor(worker_ip)
        vnc_mgr  = VNCPortManager()

        try:
            rec0 = records[0]
            with SSHClient(worker_ip, rec0["ssh_user"], rec0["ssh_private_key"]) as ssh:
                for record in records:
                    vm_id = record["vm_id"]
                    try:
                        executor.kill_vm(ssh, vm_id, slice_id)
                        executor.delete_disk(ssh, vm_id, slice_id)
                        if record.get("vnc_port"):
                            vnc_mgr.release_port(worker_ip, record["vnc_port"])
                        destroyed.append(vm_id)
                    except Exception as exc:
                        logger.error(f"[worker={worker_ip}] No se pudo destruir {vm_id}: {exc}")
                        failed.append(vm_id)
        except Exception as exc:
            logger.error(f"[worker={worker_ip}] Fallo SSH en destrucción: {exc}")
            for record in records:
                failed.append(record["vm_id"])

        return destroyed, failed

    @staticmethod
    def _compute_status(successful, failed, total) -> ProvisioningStatus:
        if not failed:
            return ProvisioningStatus.SUCCESS
        if not successful:
            return ProvisioningStatus.ERROR
        return ProvisioningStatus.PARTIAL
