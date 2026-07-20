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
import asyncio
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
from app.services.openstack_compute_executor import OpenStackComputeExecutor, get_connection

logger = logging.getLogger(__name__)


class Provisioner:
    """Orquesta el ciclo de vida completo de las VMs."""

    def __init__(self):
        pass

    # ------------------------------------------------------------------
    # Deploy
    # ------------------------------------------------------------------

    async def deploy(self, request: DeployRequest) -> DeployReply:
        logger.info(
            "Deploy slice=%s, request=%s, vms=%d, AZ=%d",
            request.slice_id, request.request_id, len(request.vms), request.availability_zone_id
        )

        if request.availability_zone_id == 2:
            # Estrategia: OpenStack Nova API
            logger.info(f"[CP] Strategy: OpenStack (slice_id={request.slice_id})")
            try:
                conn = await asyncio.to_thread(get_connection)
                os_executor = OpenStackComputeExecutor()
                tasks = [os_executor.deploy_vm(conn, vm, request.slice_id) for vm in request.vms]
                results = await asyncio.gather(*tasks)
                # NOTA: el Q-in-Q (802.1ad) de OpenStack lo aplica ahora el
                # NetworkOrchestrator en el paso de RED (pre-compute), usando el
                # host_map del placement — igual que en Linux Cluster.
            except Exception as e:
                logger.error(f"[CP] Error conectando a OpenStack: {e}")
                results = [VMResult(vm_id=vm.vm_id, worker_ip=vm.worker_ip, error=str(e)) for vm in request.vms]
        else:
            # Estrategia: Linux Cluster (QEMU/KVM local)
            logger.info(f"[CP] Strategy: Linux Cluster (slice_id={request.slice_id})")
            tasks = [asyncio.to_thread(self._deploy_vm_sync, vm, request.slice_id) for vm in request.vms]
            results = await asyncio.gather(*tasks)

        ok     = [r for r in results if r.error is None]
        failed = [r for r in results if r.error is not None]

        if not failed:
            status = DeployStatus.SUCCESS
        elif not ok:
            status = DeployStatus.ERROR
        else:
            status = DeployStatus.PARTIAL

        logger.info("[CP] 📊 Resultado deploy slice=%s: ✅ %d OK  ❌ %d fallidas  estado=%s",
                    request.slice_id, len(ok), len(failed), status.value)
        return DeployReply(
            slice_id=request.slice_id,
            request_id=request.request_id,
            status=status,
            vms=ok,
            failed_vms=failed,
        )

    async def refresh_console(self, provider_instance_id: str) -> Optional[str]:
        """Pide a Nova un token de consola noVNC nuevo para una instancia ya desplegada."""
        conn = await asyncio.to_thread(get_connection)
        os_executor = OpenStackComputeExecutor()
        return await os_executor.get_console_token(conn, provider_instance_id)

    def _deploy_vm_sync(self, vm: VMSpec, slice_id: str) -> VMResult:
        # ── Modo Edición (REQ-US-14): VM ya corriendo — solo hot-plug de NICs ──
        if getattr(vm, "already_deployed", False):
            return self._hotplug_vm_sync(vm, slice_id)

        vnc_port = vm.vnc_port
        vnc_display = vnc_port - 5900

        logger.info("[CP] 🚀 Desplegando VM: %s  worker=%s  vCPUs=%d  RAM=%d MB  Disco=%d GB",
                    vm.vm_id, vm.worker_ip, vm.vcpus, vm.ram_mb, vm.disk_gb)
        logger.info("[CP]    imagen=%s  vnc_port=%d  taps=%d  user=%s",
                    vm.image_path, vnc_port, len(vm.tap_interfaces or []), vm.vm_user or 'ubuntu')

        for attempt in range(1, settings.SSH_MAX_RETRIES + 1):
            try:
                logger.info("[CP]    🔌 Abriendo SSH a %s:%d (intento %d/%d)...",
                            vm.worker_ip, vm.worker_port, attempt, settings.SSH_MAX_RETRIES)
                with SSHClient(vm.worker_ip, vm.ssh_user, vm.ssh_private_key, port=vm.worker_port) as ssh:
                    executor = QEMUExecutor(ssh)

                    # 1. Disco
                    logger.info("[CP]    💽 [1/3] Creando disco QCOW2 para %s...", vm.vm_id)
                    disk_path = executor.create_disk(vm.vm_id, slice_id, vm.image_path, vm.worker_ip, vm.disk_gb)
                    logger.info("[CP]    💽       Disco creado: %s", disk_path)

                    # 2. TAP interfaces (Eliminado: ahora delegado 100% al NetworkOrchestrator)
                    if vm.tap_interfaces:
                        logger.info("[CP]    🔗 [2/3] Interfaces TAP provistas por NetworkOrchestrator: %d", len(vm.tap_interfaces))
                        for tap in vm.tap_interfaces:
                            logger.info("[CP]          TAP: %-28s MAC: %s",
                                        getattr(tap, 'tap_name', tap), getattr(tap, 'mac', ''))

                    # 3. Lanzar QEMU
                    logger.info("[CP]    ⚡ [3/3] Lanzando QEMU (VNC :%d)...", vnc_display)
                    pid = executor.launch_vm(
                        vm_id=vm.vm_id,
                        slice_id=slice_id,
                        disk_path=disk_path,
                        vcpus=vm.vcpus,
                        ram_mb=vm.ram_mb,
                        vnc_display=vnc_display,
                        tap_interfaces=vm.tap_interfaces,
                        image_path=vm.image_path,
                        vm_user=vm.vm_user or "ubuntu",
                        vm_password=vm.vm_password or "pucp2026",
                        priority=vm.priority,
                        owner_ssh_key=getattr(vm, "owner_ssh_public_key", None) or "",
                        image_default_username=getattr(vm, "image_default_username", None) or "",
                    )
                    logger.info("[CP] ✅ VM %s activa en worker=%s  PID=%s  VNC=:%d (port %d)",
                                vm.vm_id, vm.worker_ip, pid, vnc_display, vnc_port)
                    return VMResult(
                        vm_id=vm.vm_id,
                        worker_ip=vm.worker_ip,
                        pid=pid,
                        vnc_port=vnc_port,
                    )

            except Exception as exc:
                logger.warning("[CP] ⚠️  VM %s intento %d/%d falló: %s",
                               vm.vm_id, attempt, settings.SSH_MAX_RETRIES, exc)
                if attempt == settings.SSH_MAX_RETRIES:
                    logger.error("[CP] ❌ VM %s no se pudo desplegar tras %d intentos",
                                 vm.vm_id, settings.SSH_MAX_RETRIES)
                    return VMResult(
                        vm_id=vm.vm_id,
                        worker_ip=vm.worker_ip,
                        error=str(exc),
                    )
                time.sleep(settings.SSH_RETRY_DELAY)

    def _hotplug_vm_sync(self, vm: VMSpec, slice_id: str) -> VMResult:
        """
        Conecta en caliente las NICs nuevas (vm.tap_interfaces) a una VM QEMU
        ya desplegada, vía QMP. Los TAPs ya existen (paso NetworkOrchestrator).
        """
        taps = vm.tap_interfaces or []
        logger.info("[CP] 🔌 Hot-plug VM existente %s en worker=%s: %d NIC(s)",
                    vm.vm_id, vm.worker_ip, len(taps))
        if not taps:
            return VMResult(vm_id=vm.vm_id, worker_ip=vm.worker_ip, vnc_port=vm.vnc_port)

        try:
            with SSHClient(vm.worker_ip, vm.ssh_user, vm.ssh_private_key, port=vm.worker_port) as ssh:
                executor = QEMUExecutor(ssh)
                for tap in taps:
                    tap_name = getattr(tap, "tap_name", None) or tap.get("tap_name")
                    mac      = getattr(tap, "mac", None) or tap.get("mac")
                    executor.hotplug_nic(vm.vm_id, slice_id, tap_name, mac)
            logger.info("[CP] ✅ Hot-plug completado para VM %s", vm.vm_id)
            return VMResult(vm_id=vm.vm_id, worker_ip=vm.worker_ip, vnc_port=vm.vnc_port)
        except Exception as exc:
            logger.error("[CP] ❌ Hot-plug falló para VM %s: %s", vm.vm_id, exc)
            return VMResult(vm_id=vm.vm_id, worker_ip=vm.worker_ip, error=str(exc))

    # ------------------------------------------------------------------
    # Destroy
    # ------------------------------------------------------------------

    async def destroy(self, request: DestroyRequest, vm_records: List[dict]) -> DestroyReply:
        """
        Destruye las VMs de un slice.
        vm_records: lista de dicts con vm_id, worker_ip, ssh_user, ssh_private_key,
                    y tap_interfaces (si aplica). Viene del KV via worker.py.
        """
        logger.info("Destroy slice=%s, vms=%d, AZ=%d", request.slice_id, len(vm_records), request.availability_zone_id)

        unplugs = getattr(request, "unplugs", []) or []
        is_shrink = getattr(request, "mode", "full") == "shrink"

        if not vm_records and not (is_shrink and unplugs):
            # Shrink de SOLO un enlace (sin VMs eliminadas) llega con vms=[] pero
            # unplugs=[...] — antes esto retornaba SUCCESS acá mismo sin llegar
            # nunca al bloque de abajo, así que el QMP device_del/netdev_del de
            # la NIC en la VM viva nunca se ejecutaba (el TAP del host sí se
            # borraba desde NetworkOrchestrator, pero la interfaz quedaba viva
            # dentro del QEMU en ejecución).
            logger.warning("No hay VMs ni unplugs para slice=%s", request.slice_id)
            return DestroyReply(
                slice_id=request.slice_id,
                request_id=request.request_id,
                status=DeployStatus.SUCCESS,
            )

        if request.availability_zone_id == 2:
            # Estrategia: OpenStack Nova API
            logger.info(f"[CP] Strategy: OpenStack (slice_id={request.slice_id})")
            try:
                conn = await asyncio.to_thread(get_connection)
                os_executor = OpenStackComputeExecutor()
                # Shrink: primero desconectar interfaces de las VMs sobrevivientes
                if is_shrink and unplugs:
                    for up in unplugs:
                        await os_executor.detach_interface(conn, up, request.slice_id)
                tasks = [os_executor.destroy_vm(conn, record, request.slice_id) for record in vm_records]
                errors = await asyncio.gather(*tasks)
                # NOTA: el teardown Q-in-Q de OpenStack lo hace ahora el
                # NetworkOrchestrator en el paso de RED (destroy).
            except Exception as e:
                logger.error(f"[CP] Error conectando a OpenStack durante destroy: {e}")
                errors = [str(e)] * len(vm_records)
        else:
            # Estrategia: Linux Cluster (SSH/QEMU)
            logger.info(f"[CP] Strategy: Linux Cluster (slice_id={request.slice_id})")
            # Shrink: hot-unplug de las NICs en las VMs sobrevivientes
            if is_shrink and unplugs:
                for up in unplugs:
                    try:
                        await asyncio.to_thread(self._unplug_sync, up, request.slice_id)
                    except Exception as exc:
                        logger.error(f"[CP] Unplug falló para VM {up.get('vm_id')}: {exc}")
            tasks = [asyncio.to_thread(self._destroy_vm_sync, record, request.slice_id) for record in vm_records]
            errors = await asyncio.gather(*tasks)

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

    def _unplug_sync(self, up: dict, slice_id: str) -> None:
        """Hot-unplug de una NIC en una VM sobreviviente (Linux Cluster)."""
        vm_id     = up.get("vm_id")
        tap_name  = up.get("tap_name")
        if not vm_id or not tap_name:
            return
        with SSHClient(up.get("worker_ip"), up.get("ssh_user"),
                       up.get("ssh_private_key") or "", port=int(up.get("worker_port") or 22)) as ssh:
            QEMUExecutor(ssh).unplug_nic(vm_id, slice_id, tap_name)

    def _destroy_vm_sync(self, record: dict, slice_id: str) -> Optional[str]:
        vm_id      = record["vm_id"]
        worker_ip  = record["worker_ip"]
        ssh_user   = record["ssh_user"]
        ssh_key    = record.get("ssh_private_key") or ""
        worker_port = int(record.get("worker_port") or 22)
        tap_ifaces = record.get("tap_interfaces", [])

        logger.debug(
            "[CP][DESTROY] VM=%s  worker=%s:%d  user=%s  key_len=%d",
            vm_id, worker_ip, worker_port, ssh_user, len(ssh_key)
        )
        if not ssh_key.strip():
            logger.error("[CP][DESTROY] ¡ssh_private_key VACÍA para VM=%s! worker=%s:%d",
                         vm_id, worker_ip, worker_port)

        try:
            with SSHClient(worker_ip, ssh_user, ssh_key, port=worker_port) as ssh:
                executor = QEMUExecutor(ssh)

                executor.kill_vm(vm_id, slice_id)

                executor.delete_disk(vm_id, slice_id)
                executor.delete_seed_iso(vm_id)  # Limpia el ISO de cloud-init

            logger.info("VM %s destruida correctamente", vm_id)
            return None

        except Exception as exc:
            logger.error("Error destruyendo VM %s: %s", vm_id, exc)
            return str(exc)
