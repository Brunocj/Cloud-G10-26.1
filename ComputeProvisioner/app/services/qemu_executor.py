"""
Ejecutor QEMU/KVM sobre workers remotos vía SSH.

Responsabilidades:
  - Crear disco QCOW2 con backing file (thin provisioning).
  - Crear interfaces TAP y conectarlas al bridge OVS (br-int).
  - Lanzar proceso QEMU/KVM con las TAPs y MACs asignadas.
  - Obtener el PID del proceso lanzado.
  - Destruir VMs: matar proceso, eliminar disco, limpiar TAPs.
"""

import logging
from typing import List

from app.core.config import settings
from app.models.schemas import TapInterface
from app.services.ssh_client import SSHClient
from app.utils.image_resolver import get_image_path, get_vm_disk_path

logger = logging.getLogger(__name__)


class QEMUExecutor:
    """
    Ejecuta operaciones QEMU/KVM en un worker remoto.
    Cada instancia opera sobre un único worker (una conexión SSH).
    """

    def __init__(self, ssh: SSHClient):
        self._ssh = ssh

    def _exec_checked(self, command: str) -> str:
        """Ejecuta un comando y lanza excepción si el exit code != 0."""
        code, out, err = self._ssh.exec(command)
        if code != 0:
            raise RuntimeError(
                f"Comando falló (exit {code}):\n  cmd: {command}\n  stderr: {err}"
            )
        return out

    # ------------------------------------------------------------------
    # Deploy
    # ------------------------------------------------------------------

    # 🔥 FIX: Añadimos disk_gb como parámetro
    def create_disk(self, vm_id: str, slice_id: str, image_path: str, worker_ip: str, disk_gb: float) -> str:
        """
        Crea un disco QCOW2 con thin provisioning (backing file) y tamaño específico.
        """
        disk_path = get_vm_disk_path(vm_id, slice_id)
        
        # Convertimos GB a la sintaxis que entiende qemu-img (ej: 10G)
        size_arg = f"{int(disk_gb)}G"

        # 🔥 FIX: Añadimos el tamaño al final del comando
        self._exec_checked(
            f"sudo qemu-img create -f qcow2 -b {image_path} -F qcow2 {disk_path} {size_arg}"
        )
        logger.info("Disco creado: %s usando imagen %s con tamaño %s", disk_path, image_path, size_arg)
        return disk_path

    def create_tap_interfaces(self, tap_interfaces: List[TapInterface]) -> None:
        """
        Crea interfaces TAP en el worker y las conecta al bridge OVS (br-int).

        Para cada TapInterface:
          1. ip tuntap add dev {tap_name} mode tap
          2. ovs-vsctl add-port {OVS_BRIDGE} {tap_name}
          3. ip link set {tap_name} up
        """
        bridge = settings.OVS_BRIDGE

        for iface in tap_interfaces:
            tap = iface.tap_name
            logger.info("Creando TAP %s → %s", tap, bridge)
            self._exec_checked(f"sudo ip tuntap add dev {tap} mode tap")
            self._exec_checked(f"sudo ovs-vsctl add-port {bridge} {tap}")
            self._exec_checked(f"sudo ip link set {tap} up")
            logger.info("TAP %s conectada a OVS bridge %s", tap, bridge)

    def launch_vm(
        self,
        vm_id:          str,
        slice_id:       str,
        disk_path:      str,
        vcpus:          int,
        ram_mb:         int,
        vnc_display:    int,
        tap_interfaces: List[TapInterface],
        priority:       int = 0,
    ) -> int:
        """
        Lanza el proceso QEMU/KVM en el worker.
        Returns: PID del proceso QEMU.
        """
        name    = f"{vm_id}-{slice_id}"
        net_args = _build_net_args(tap_interfaces)

        # En qemu_executor.py, dentro de launch_vm
        cmd = (
            f"sudo nice -n {priority} "
            f"qemu-system-x86_64 "
            f"-enable-kvm "
            f"-name {name} "
            f"-m {ram_mb} "
            f"-smp {vcpus} "
            f"-drive file={disk_path},format=qcow2 "
            # 🔥 FIX: Agregamos 0.0.0.0 y websocket=on
            f"-vnc 0.0.0.0:{vnc_display},websocket=on " 
            f"{net_args}"
            f"-daemonize"
        )

        self._exec_checked(cmd)
        pid = self._get_pid(name)
        logger.info("VM %s lanzada, PID=%d, VNC=:%d", name, pid, vnc_display)
        return pid

    def _get_pid(self, vm_name: str) -> int:
        out = self._exec_checked(f"pgrep -f 'name {vm_name}'")
        try:
            return int(out.splitlines()[0])
        except (ValueError, IndexError) as exc:
            raise RuntimeError(
                f"No se pudo obtener PID para '{vm_name}': {out}"
            ) from exc

    # ------------------------------------------------------------------
    # Destroy
    # ------------------------------------------------------------------

    def kill_vm(self, vm_id: str, slice_id: str) -> None:
        """Mata el proceso QEMU de la VM si está corriendo."""
        name = f"{vm_id}-{slice_id}"
        code, _, _ = self._ssh.exec(f"pgrep -f 'name {name}'")
        if code == 0:
            self._ssh.exec(f"sudo pkill -f 'name {name}'")
            logger.info("VM %s terminada", name)
        else:
            logger.warning("VM %s no encontrada (ya estaba muerta)", name)

    def delete_disk(self, vm_id: str, slice_id: str) -> None:
        """Elimina el disco QCOW2 de la VM."""
        disk_path = get_vm_disk_path(vm_id, slice_id)
        code, _, _ = self._ssh.exec(f"test -f {disk_path}")
        if code == 0:
            self._exec_checked(f"sudo rm -f {disk_path}")
            logger.info("Disco eliminado: %s", disk_path)
        else:
            logger.warning("Disco no encontrado (ya eliminado): %s", disk_path)

    def destroy_tap_interfaces(self, tap_interfaces: List[TapInterface]) -> None:
        """
        Elimina interfaces TAP del worker y del bridge OVS.

        Para cada TapInterface:
          1. ovs-vsctl del-port {OVS_BRIDGE} {tap_name}
          2. ip tuntap del dev {tap_name} mode tap
        """
        bridge = settings.OVS_BRIDGE

        for iface in tap_interfaces:
            tap = iface.tap_name
            logger.info("Eliminando TAP %s de %s", tap, bridge)

            code, _, _ = self._ssh.exec(f"ip link show {tap}")
            if code != 0:
                logger.warning("TAP %s no encontrada, omitiendo", tap)
                continue

            self._ssh.exec(f"sudo ovs-vsctl del-port {bridge} {tap}")
            self._ssh.exec(f"sudo ip tuntap del dev {tap} mode tap")
            logger.info("TAP %s eliminada", tap)


# ------------------------------------------------------------------
# Helpers privados
# ------------------------------------------------------------------

def _build_net_args(tap_interfaces: List[TapInterface]) -> str:
    """
    Construye los argumentos -netdev/-device para QEMU.

    Ejemplo con 2 TAPs:
      -netdev tap,id=net0,ifname=tap-vm1-0,script=no,downscript=no
      -device virtio-net-pci,netdev=net0,mac=52:54:00:A3:C7:00
      -netdev tap,id=net1,ifname=tap-vm1-1,script=no,downscript=no
      -device virtio-net-pci,netdev=net1,mac=52:54:00:A3:C7:01
    """
    if not tap_interfaces:
        return "-netdev user,id=net0 -device virtio-net-pci,netdev=net0 "

    parts = []
    for i, iface in enumerate(tap_interfaces):
        net_id = f"net{i}"
        parts.append(
            f"-netdev tap,id={net_id},ifname={iface.tap_name},"
            f"script=no,downscript=no "
        )
        parts.append(
            f"-device virtio-net-pci,netdev={net_id},mac={iface.mac} "
        )
    return "".join(parts)
