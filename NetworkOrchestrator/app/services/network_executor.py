"""
Ejecutor de comandos crudos de Open vSwitch e iptables en los workers.
"""

import logging
from app.services.ssh_client import SSHClient
from app.models.schemas import SecurityRule

logger = logging.getLogger(__name__)

class NetworkExecutor:
    def __init__(self, worker_ip: str):
        self.worker_ip = worker_ip

    def configure_vlan_and_port(self, ssh: SSHClient, tap_interface: str, vlan_id: int) -> None:
        """Conecta el TAP al switch virtual y le asigna la VLAN (Aislamiento Capa 2)."""
        
        # 1. Asegurar que el switch de integración exista
        ssh.exec("sudo ovs-vsctl --may-exist add-br br-int")
        
        # 2. Enchufar la interfaz virtual (TAP) al switch
        exit_code, _, err = ssh.exec(f"sudo ovs-vsctl --may-exist add-port br-int {tap_interface}")
        if exit_code != 0:
            raise RuntimeError(f"Fallo al enchufar {tap_interface} a OVS: {err}")
            
        # 3. Asignar la VLAN de aislamiento para el slice
        exit_code, _, err = ssh.exec(f"sudo ovs-vsctl set port {tap_interface} tag={vlan_id}")
        if exit_code != 0:
            raise RuntimeError(f"Fallo al configurar VLAN {vlan_id} en {tap_interface}: {err}")
            
        logger.info(f"[{self.worker_ip}] Enlace configurado: {tap_interface} -> VLAN {vlan_id}")

    def apply_security_groups(self, ssh: SSHClient, tap_interface: str, rules: list[SecurityRule]) -> None:
        """Aplica el firewall básico usando iptables."""
        if not rules:
            return

        # (Opcional) Limpiar reglas previas si fuera necesario para este TAP
        # ssh.exec(f"sudo iptables -D FORWARD -m physdev --physdev-out {tap_interface} ...")

        for rule in rules:
            cmd = (f"sudo iptables -I FORWARD 1 -m physdev --physdev-out {tap_interface} "
                   f"-p {rule.protocol} --dport {rule.allow_port} -j ACCEPT")
            exit_code, _, err = ssh.exec(cmd)
            if exit_code != 0:
                logger.warning(f"[{self.worker_ip}] Fallo al aplicar firewall {rule}: {err}")
            else:
                logger.debug(f"[{self.worker_ip}] FW Permitido: {rule.protocol}/{rule.allow_port} -> {tap_interface}")

    def destroy_port(self, ssh: SSHClient, tap_interface: str) -> None:
        """Desconecta el TAP del switch virtual durante la destrucción del slice."""
        exit_code, _, err = ssh.exec(f"sudo ovs-vsctl --if-exists del-port br-int {tap_interface}")
        if exit_code == 0:
            logger.info(f"[{self.worker_ip}] Puerto {tap_interface} eliminado de OVS")
        else:
            logger.warning(f"[{self.worker_ip}] Error borrando puerto OVS {tap_interface}: {err}")