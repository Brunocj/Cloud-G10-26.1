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

    def configure_gateway_and_nat(self, ssh: SSHClient, slice_id: str, vms: list, mgmt_vlan: int) -> None:
        """
        Crea el Gateway virtual, levanta DHCP y aplica reglas NAT.
        """
        # Usamos los últimos 4 caracteres del slice para un nombre corto
        gw_name = f"gw_{str(slice_id)[-4:]}" 
        
        # 🔥 1. LA NUEVA MAGIA: Redes 10.0.0.0/8 para soportar >65,000 Slices
        octeto_2 = (int(slice_id) // 256) % 256
        octeto_3 = int(slice_id) % 256
        subred_interna = f"10.{octeto_2}.{octeto_3}"
        gw_ip = f"{subred_interna}.1/24"
        
        try:
            # 1. Crear el puerto Gateway con la VLAN ÚNICA del slice
            ssh.exec(f"sudo ovs-vsctl --may-exist add-port br-int {gw_name} tag={mgmt_vlan} -- set interface {gw_name} type=internal")
            ssh.exec(f"sudo ip addr add {gw_ip} dev {gw_name} || true")
            ssh.exec(f"sudo ip link set {gw_name} up")
            ssh.exec("sudo sysctl -w net.ipv4.ip_forward=1")

            # 🔥 2. CONFIGURAR DHCP (DNSMASQ) CON IPs ESTÁTICAS
            # Matamos cualquier proceso DHCP anterior que se haya quedado colgado
            ssh.exec(f"sudo kill $(cat /var/run/dnsmasq-{gw_name}.pid) 2>/dev/null || true")
            
            # Leemos las MACs de las VMs para amarrarlas a la IP interna
            dhcp_hosts_args = ""
            for vm in vms:
                if getattr(vm, 'internal_ip', None) and vm.tap_interfaces:
                    mac_principal = vm.tap_interfaces[0].mac
                    ip_interna = vm.internal_ip
                    dhcp_hosts_args += f"--dhcp-host={mac_principal},{ip_interna} "

            # Levantamos el DHCP atado EXCLUSIVAMENTE a la interfaz gw_xxxx
            cmd_dhcp = (
                f"sudo dnsmasq --strict-order --except-interface=lo "
                f"--interface={gw_name} "
                f"--listen-address={subred_interna}.1 "
                f"--dhcp-range={subred_interna}.10,{subred_interna}.200 "
                f"--dhcp-option=option:router,{subred_interna}.1 "
                f"{dhcp_hosts_args}"  # Inyectamos los amarres MAC->IP
                f"--pid-file=/var/run/dnsmasq-{gw_name}.pid"
            )
            ssh.exec(cmd_dhcp)
            logger.info(f"[{self.worker_ip}] DHCP levantado en {gw_name} ({subred_interna}.0/24)")
            
            # 3. Iterar sobre las VMs para aplicar Iptables
            for vm in vms:
                # REGLA A: Salida a Internet (SNAT)
                if getattr(vm, 'internet_access', 0) == 1:
                    cmd_snat = f"sudo iptables -t nat -A POSTROUTING -s {subred_interna}.0/24 -o ens3 -j MASQUERADE"
                    ssh.exec(cmd_snat)
                    logger.info(f"[{self.worker_ip}] SNAT (Internet) habilitado para subred {subred_interna}.0/24")

                # REGLA B: Visibilidad Externa (DNAT)
                if getattr(vm, 'external_ip', None) and getattr(vm, 'internal_ip', None):
                    ext_ip = vm.external_ip
                    internal_vm_ip = vm.internal_ip 
                    
                    cmd_dnat = f"sudo iptables -t nat -A PREROUTING -d {ext_ip} -j DNAT --to-destination {internal_vm_ip}"
                    ssh.exec(cmd_dnat)
                    logger.info(f"[{self.worker_ip}] Ruteo Externo habilitado: {ext_ip} -> {internal_vm_ip}")

        except Exception as e:
            logger.error(f"[{self.worker_ip}] Error configurando Gateway/NAT: {e}")

    def destroy_gateway(self, ssh: SSHClient, slice_id: str) -> None:
        """Limpia la interfaz Gateway y mata el DHCP al destruir el slice."""
        gw_name = f"gw_{str(slice_id)[-4:]}"
        
        # 1. Matar el proceso DHCP
        ssh.exec(f"sudo kill $(cat /var/run/dnsmasq-{gw_name}.pid) 2>/dev/null || true")
        ssh.exec(f"sudo rm -f /var/run/dnsmasq-{gw_name}.pid")
        
        # 2. Borrar el puerto del switch virtual
        ssh.exec(f"sudo ovs-vsctl --if-exists del-port br-int {gw_name}")
        
        # (Opcional pero recomendado: Limpiar las reglas de iptables)
        # Como es un entorno educativo, usualmente se puede dejar que el firewall se limpie con reinicios,
        # pero en producción aquí haríamos un iptables -D para revertir el PREROUTING y POSTROUTING.
        
        logger.info(f"[{self.worker_ip}] Gateway {gw_name} y DHCP eliminados.")