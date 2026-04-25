"""
Ejecutor QEMU/KVM.
Construye y ejecuta comandos para crear discos thin y lanzar VMs.
Estrictamente limitado a operaciones de cómputo — sin configuración de red.
"""

import logging
from typing import Optional

from app.services.ssh_client import SSHClient
from app.utils.image_resolver import get_image_path, get_vm_disk_path

logger = logging.getLogger(__name__)


class QEMUExecutor:
    """
    Encapsula todas las operaciones QEMU/KVM sobre un worker.
    Se instancia por worker.
    """

    def __init__(self, worker_ip: str):
        self.worker_ip = worker_ip

    # ── Disco ───────────────────────────────────────────────────────────────

    def create_thin_disk(self, ssh: SSHClient, vm_id: str,
                         slice_id: str, image_name: str) -> str:
        """
        Crea un disco QCOW2 con thin provisioning (backing file = imagen base).
        Retorna la ruta del disco creado en el worker.
        """
        from app.core.config import settings

        base_path = get_image_path(image_name, self.worker_ip)
        disk_path = get_vm_disk_path(vm_id, slice_id)

        ssh.exec(f"mkdir -p {settings.VMS_BASE_DIR}")

        cmd = (
            f"qemu-img create -f qcow2 "
            f"-b {base_path} "
            f"-F qcow2 "
            f"{disk_path}"
        )
        exit_code, _, err = ssh.exec(cmd)
        if exit_code != 0:
            raise RuntimeError(f"qemu-img create falló para {vm_id}: {err}")

        logger.info(f"[{self.worker_ip}] Disco thin creado: {disk_path}")
        return disk_path

    def delete_disk(self, ssh: SSHClient, vm_id: str, slice_id: str) -> None:
        """Elimina el disco thin de una VM."""
        disk_path = get_vm_disk_path(vm_id, slice_id)
        exit_code, _, err = ssh.exec(f"rm -f {disk_path}")
        if exit_code != 0:
            logger.warning(f"[{self.worker_ip}] No se pudo eliminar disco {disk_path}: {err}")
        else:
            logger.info(f"[{self.worker_ip}] Disco eliminado: {disk_path}")

    # ── VM ──────────────────────────────────────────────────────────────────

    def launch_vm(self, ssh: SSHClient, vm_id: str, slice_id: str,
                  disk_path: str, vcpus: int, ram_mb: int,
                  vnc_port: int, priority: int = 0) -> int:
        """
        Lanza una VM con QEMU/KVM en el worker.
        Retorna el PID del proceso QEMU.
        """
        cmd = self._build_qemu_command(
            vm_id, slice_id, disk_path, vcpus, ram_mb, vnc_port, priority
        )
        exit_code, _, err = ssh.exec(cmd)
        if exit_code != 0:
            raise RuntimeError(f"QEMU no arrancó para {vm_id}: {err}")

        pid = self._get_pid(ssh, vm_id, slice_id)
        if pid is None:
            raise RuntimeError(f"QEMU lanzado pero PID no encontrado para {vm_id}")

        logger.info(f"[{self.worker_ip}] VM {vm_id} corriendo con PID {pid}")
        return pid

    def kill_vm(self, ssh: SSHClient, vm_id: str, slice_id: str) -> bool:
        """
        Detiene una VM por nombre. Retorna True si se terminó exitosamente.
        """
        vm_name = self._vm_name(vm_id, slice_id)
        exit_code, out, _ = ssh.exec(f"pgrep -f 'name {vm_name}'")
        if exit_code != 0 or not out:
            logger.warning(f"[{self.worker_ip}] VM {vm_id} no encontrada (ya apagada?)")
            return True

        pid = out.strip().split()[0]
        exit_code, _, err = ssh.exec(f"kill -9 {pid}")
        if exit_code != 0:
            logger.error(f"[{self.worker_ip}] No se pudo matar PID {pid}: {err}")
            return False

        logger.info(f"[{self.worker_ip}] VM {vm_id} terminada (PID {pid})")
        return True

    # ── Helpers ─────────────────────────────────────────────────────────────

    def _build_qemu_command(self, vm_id: str, slice_id: str, disk_path: str,
                            vcpus: int, ram_mb: int,
                            vnc_port: int, priority: int = 0) -> str:
        """
        Construye el comando qemu-system-x86_64.
        Sin argumentos de red — la configuración de red es responsabilidad
        del Network Orchestrator.
        """
        vm_name = self._vm_name(vm_id, slice_id)
        # vnc_port es el puerto completo (ej: 5901), QEMU espera el display (ej: 1)
        vnc_display = vnc_port - 5900

        cmd = (
            f"qemu-system-x86_64"
            f" -enable-kvm"
            f" -name {vm_name}"
            f" -m {ram_mb}"
            f" -smp {vcpus}"
            f" -vnc 0.0.0.0:{vnc_display}"
            f" -daemonize"
            f" {disk_path}"
        )

        if priority and priority > 0:
            cmd = f"nice -n {priority} {cmd}"

        return cmd

    def _get_pid(self, ssh: SSHClient, vm_id: str, slice_id: str) -> Optional[int]:
        vm_name = self._vm_name(vm_id, slice_id)
        exit_code, out, _ = ssh.exec(f"pgrep -f 'name {vm_name}'")
        if exit_code == 0 and out:
            try:
                return int(out.strip().split()[0])
            except ValueError:
                pass
        return None

    @staticmethod
    def _vm_name(vm_id: str, slice_id: str) -> str:
        """Nombre único del proceso QEMU: vm_id-slice_id."""
        return f"{vm_id}-{slice_id}"
