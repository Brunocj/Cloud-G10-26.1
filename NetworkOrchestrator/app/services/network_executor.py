"""
Ejecutor de comandos crudos de Open vSwitch e iptables en los workers.
"""

import logging
from app.services.ssh_client import SSHClient
from app.models.schemas import SecurityRule

from app.core.config import settings

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
        Crea el Gateway virtual, levanta DHCP y aplica reglas NAT e IPs Externas.
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

            # 🔥 NUEVO: Permitir tráfico DHCP entrante desde las VMs hacia el Gateway
            ssh.exec(f"sudo iptables -I INPUT -i {gw_name} -p udp --dport 67:68 -j ACCEPT")

            # 🔥 2. CONFIGURAR DHCP (DNSMASQ) CON IPs ESTÁTICAS
            # Matamos cualquier proceso DHCP anterior que se haya quedado colgado
            ssh.exec(f"sudo kill $(cat /var/run/dnsmasq-{gw_name}.pid) 2>/dev/null || true")
            
            # Leemos las MACs de las VMs para amarrarlas a la IP interna
            dhcp_hosts_args = ""
            for vm in vms:
                if getattr(vm, 'internal_ip', None) and vm.tap_interfaces:
                    mac_principal = vm.tap_interfaces[0].mac
                    ip_interna = vm.internal_ip
                    # Marcamos como host conocido para evitar asignación dinámica inesperada
                    dhcp_hosts_args += f"--dhcp-host={mac_principal},{ip_interna},set:known "

            # Limpiamos leases viejos para evitar que dnsmasq entregue IPs antiguas
            ssh.exec(f"sudo rm -f /var/run/dnsmasq-{gw_name}.leases")

            # Levantamos el DHCP atado EXCLUSIVAMENTE a la interfaz gw_xxxx
            cmd_dhcp = (
                f"sudo dnsmasq --strict-order --except-interface=lo --bind-interfaces "
                f"--interface={gw_name} "
                f"--listen-address={subred_interna}.1 "
                f"--dhcp-range={subred_interna}.10,{subred_interna}.200 "
                f"--dhcp-option=option:router,{subred_interna}.1 "
                f"--dhcp-authoritative "
                f"--dhcp-ignore=tag:!known "
                f"--dhcp-leasefile=/var/run/dnsmasq-{gw_name}.leases "
                f"--log-dhcp --log-queries "
                f"{dhcp_hosts_args}"  # Inyectamos los amarres MAC->IP
                f"--pid-file=/var/run/dnsmasq-{gw_name}.pid"
            )
            ssh.exec(cmd_dhcp)
            logger.info(f"[{self.worker_ip}] DHCP levantado en {gw_name} ({subred_interna}.0/24)")
            
            # 3. Iterar sobre las VMs para aplicar Iptables
            for vm in vms:
                internal_vm_ip = getattr(vm, 'internal_ip', None)

                # REGLA A: Salida a Internet (SNAT) por IP
                if getattr(vm, 'internet_access', 0) == 1 and internal_vm_ip:
                    cmd_snat = (
                        f"sudo iptables -t nat -A POSTROUTING -s {internal_vm_ip}/32 "
                        f"-o {settings.WAN_INTERFACE} -j MASQUERADE"
                    )
                    ssh.exec(cmd_snat)
                    # Permitimos forward de salida y respuestas
                    ssh.exec(
                        f"sudo iptables -I FORWARD 1 -s {internal_vm_ip}/32 "
                        f"-o {settings.WAN_INTERFACE} -j ACCEPT"
                    )
                    ssh.exec(
                        f"sudo iptables -I FORWARD 1 -d {internal_vm_ip}/32 "
                        f"-i {settings.WAN_INTERFACE} -m state --state ESTABLISHED,RELATED -j ACCEPT"
                    )
                    logger.info(f"[{self.worker_ip}] SNAT habilitado en {settings.WAN_INTERFACE} para {internal_vm_ip}")
                elif internal_vm_ip:
                    # Bloquear salida para VMs sin permiso de internet
                    ssh.exec(
                        f"sudo iptables -I FORWARD 1 -s {internal_vm_ip}/32 "
                        f"-o {settings.WAN_INTERFACE} -j DROP"
                    )
                    logger.info(f"[{self.worker_ip}] Bloqueo de salida aplicado para {internal_vm_ip}")

                # REGLA B: Visibilidad Externa (DNAT)
                if getattr(vm, 'external_ip', None) and internal_vm_ip:
                    ext_ip = vm.external_ip

                    # 🔥 Acceso exterior: DNAT por la interfaz de datos (ens4) hacia la VM interna
                    ssh.exec(f"sudo ip addr add {ext_ip}/32 dev {settings.EXTERNAL_INTERFACE} || true")

                    cmd_dnat = (
                        f"sudo iptables -t nat -A PREROUTING -i {settings.EXTERNAL_INTERFACE} "
                        f"-d {ext_ip} -j DNAT --to-destination {internal_vm_ip}"
                    )
                    ssh.exec(cmd_dnat)

                    # Forward explícito para permitir el flujo de entrada/salida
                    ssh.exec(
                        f"sudo iptables -I FORWARD 1 -i {settings.EXTERNAL_INTERFACE} "
                        f"-d {internal_vm_ip}/32 -j ACCEPT"
                    )
                    ssh.exec(
                        f"sudo iptables -I FORWARD 1 -o {settings.EXTERNAL_INTERFACE} "
                        f"-s {internal_vm_ip}/32 -m state --state ESTABLISHED,RELATED -j ACCEPT"
                    )

                    logger.info(
                        f"[{self.worker_ip}] DNAT exterior habilitado ({settings.EXTERNAL_INTERFACE}): "
                        f"{ext_ip} -> {internal_vm_ip}"
                    )

        except Exception as e:
            logger.error(f"[{self.worker_ip}] Error configurando Gateway/NAT: {e}")

    def destroy_gateway(self, ssh: SSHClient, slice_id: str, vms: list) -> None:
        """
        Limpia la interfaz Gateway, mata el DHCP y limpia el Iptables al destruir el slice.
        
        ORDEN CORRECTO:
        1. Limpiar iptables SNAT/DNAT
        2. Matar DHCP
        3. Borrar puerto del OVS
        4. Eliminar interfaz de Linux (mata zombis)
        5. Limpiar IPs externas
        """
        gw_name = f"gw_{str(slice_id)[-4:]}"
        octeto_2 = (int(slice_id) // 256) % 256
        octeto_3 = int(slice_id) % 256
        subred_interna = f"10.{octeto_2}.{octeto_3}"

        logger.info(f"🔥🔥🔥 [DESTROY] Iniciando destrucción del Gateway: {gw_name} en {self.worker_ip}")
        logger.info(f"🔥🔥🔥 [DESTROY] Recibí vms_list con {len(vms)} VMs")
        for i, vm in enumerate(vms):
            logger.info(f"🔥🔥🔥 [DESTROY]   VM[{i}]: id={getattr(vm, 'vm_id', '?')}, internal_ip={getattr(vm, 'internal_ip', '?')}, external_ip={getattr(vm, 'external_ip', '?')}, internet_access={getattr(vm, 'internet_access', '?')}")

        # PASO 1: Limpiar reglas de iptables PRIMERO (antes de borrar interfaces)
        logger.info(f"🔥🔥🔥 [DESTROY] PASO 1: Limpiando iptables INPUT")
        # 🔥 Limpiar la regla del Firewall DHCP
        exit_code1, out1, err1 = ssh.exec(f"sudo iptables -D INPUT -i {gw_name} -p udp --dport 67:68 -j ACCEPT || true")
        logger.info(f"🔥🔥🔥 [DESTROY]   Resultado: exit_code={exit_code1}, err={err1}")
        
        # 🔥 LIMPIEZA DE IPTABLES (El antídoto contra la basura en el kernel)
        logger.info(f"🔥🔥🔥 [DESTROY] PASO 1b: Limpiando iptables SNAT/DNAT ({len(vms)} VMs)")
        snat_count = 0
        dnat_count = 0

        if not vms:
            logger.warning(f"🔥🔥🔥 [DESTROY]   ⚠️ Sin VMs en payload. Intentando limpieza genérica por subred {subred_interna}.0/24")
            # SNAT genérico (si existe)
            exit_code_snat, _, err_snat = ssh.exec(
                f"sudo iptables -t nat -D POSTROUTING -s {subred_interna}.0/24 -o {settings.WAN_INTERFACE} -j MASQUERADE || true"
            )
            logger.info(f"🔥🔥🔥 [DESTROY]   SNAT genérico: exit_code={exit_code_snat}, err={err_snat}")

            # DNAT genérico: elimina reglas cuyo destino apunte a la subred del slice
            cmd_dnat_cleanup = (
                "sudo sh -c "
                f"\"iptables -t nat -S PREROUTING | grep -- '--to-destination {subred_interna}.' | "
                "sed 's/^-A /-D /' | while read r; do iptables -t nat $r; done\""
            )
            exit_code_dnat, _, err_dnat = ssh.exec(cmd_dnat_cleanup)
            logger.info(f"🔥🔥🔥 [DESTROY]   DNAT genérico: exit_code={exit_code_dnat}, err={err_dnat}")
        else:
            for vm in vms:
                # Borrar regla SNAT (Navegación)
                internal_vm_ip = getattr(vm, 'internal_ip', None)
                if getattr(vm, 'internet_access', 0) == 1 and internal_vm_ip:
                    cmd_snat_del = (
                        f"sudo iptables -t nat -D POSTROUTING -s {internal_vm_ip}/32 "
                        f"-o {settings.WAN_INTERFACE} -j MASQUERADE || true"
                    )
                    exit_code_snat, _, err_snat = ssh.exec(cmd_snat_del)
                    logger.info(f"🔥🔥🔥 [DESTROY]   SNAT para {internal_vm_ip}: exit_code={exit_code_snat}, err={err_snat}")
                    snat_count += 1

                    # Remover reglas de forward permitidas
                    ssh.exec(
                        f"sudo iptables -D FORWARD -s {internal_vm_ip}/32 "
                        f"-o {settings.WAN_INTERFACE} -j ACCEPT || true"
                    )
                    ssh.exec(
                        f"sudo iptables -D FORWARD -d {internal_vm_ip}/32 "
                        f"-i {settings.WAN_INTERFACE} -m state --state ESTABLISHED,RELATED -j ACCEPT || true"
                    )
                elif internal_vm_ip:
                    # Remover bloqueo para VMs sin acceso
                    ssh.exec(
                        f"sudo iptables -D FORWARD -s {internal_vm_ip}/32 "
                        f"-o {settings.WAN_INTERFACE} -j DROP || true"
                    )
                
                # Borrar regla DNAT (Acceso Externo)
                if getattr(vm, 'external_ip', None) and internal_vm_ip:
                    ext_ip = vm.external_ip
                    cmd_dnat_del = (
                        f"sudo iptables -t nat -D PREROUTING -i {settings.EXTERNAL_INTERFACE} "
                        f"-d {ext_ip} -j DNAT --to-destination {internal_vm_ip} || true"
                    )
                    exit_code_dnat, _, err_dnat = ssh.exec(cmd_dnat_del)
                    logger.info(f"🔥🔥🔥 [DESTROY]   DNAT para {ext_ip}: exit_code={exit_code_dnat}, err={err_dnat}")

                    # Remover reglas de forward para acceso exterior
                    ssh.exec(
                        f"sudo iptables -D FORWARD -i {settings.EXTERNAL_INTERFACE} "
                        f"-d {internal_vm_ip}/32 -j ACCEPT || true"
                    )
                    ssh.exec(
                        f"sudo iptables -D FORWARD -o {settings.EXTERNAL_INTERFACE} "
                        f"-s {internal_vm_ip}/32 -m state --state ESTABLISHED,RELATED -j ACCEPT || true"
                    )
                    dnat_count += 1
            logger.info(f"🔥🔥🔥 [DESTROY]   Limpiadas {snat_count} reglas SNAT y {dnat_count} reglas DNAT")

        # PASO 2: Matar el proceso DHCP
        logger.info(f"🔥🔥🔥 [DESTROY] PASO 2: Matando DHCP")
        exit_code2, out2, err2 = ssh.exec(f"sudo kill $(cat /var/run/dnsmasq-{gw_name}.pid) 2>/dev/null || true")
        logger.info(f"🔥🔥🔥 [DESTROY]   Kill DHCP: exit_code={exit_code2}, err={err2}")
        exit_code2b, out2b, err2b = ssh.exec(f"sudo rm -f /var/run/dnsmasq-{gw_name}.pid")
        logger.info(f"🔥🔥🔥 [DESTROY]   Rm PID: exit_code={exit_code2b}, err={err2b}")
        
        # PASO 3: Borrar el puerto del switch virtual OVS
        logger.info(f"🔥🔥🔥 [DESTROY] PASO 3: Borrando puerto OVS {gw_name}")
        exit_code3, out3, err3 = ssh.exec(f"sudo ovs-vsctl --if-exists del-port br-int {gw_name}")
        logger.info(f"🔥🔥🔥 [DESTROY]   OVS del-port: exit_code={exit_code3}, err={err3}")
        if exit_code3 != 0:
            logger.warning(f"🔥🔥🔥 [DESTROY]   ⚠️  Error al borrar puerto OVS: {err3}")

        # PASO 4: Eliminar la interfaz de Linux (EL MATA-ZOMBIS DEFINITIVO)
        # ✅ CRÍTICO: Esto remueve la interfaz tipo "internal" del kernel
        logger.info(f"🔥🔥🔥 [DESTROY] PASO 4: Eliminando interfaz Linux {gw_name}")
        exit_code4, out4, err4 = ssh.exec(f"sudo ip link delete {gw_name} 2>&1")
        logger.info(f"🔥🔥🔥 [DESTROY]   ip link delete: exit_code={exit_code4}, out={out4}, err={err4}")
        if exit_code4 == 0:
            logger.info(f"🔥🔥🔥 [DESTROY]   ✅ Interfaz {gw_name} eliminada exitosamente del kernel")
        else:
            # Si no existe, no es un error
            if "does not exist" in err4 or "Cannot find device" in err4:
                logger.info(f"🔥🔥🔥 [DESTROY]   ℹ️  La interfaz {gw_name} ya no existe")
            else:
                logger.warning(f"🔥🔥🔥 [DESTROY]   ⚠️  Advertencia al eliminar {gw_name}: {err4}")
        
        # PASO 5: Liberar IPs externas del host físico
        logger.info(f"🔥🔥🔥 [DESTROY] PASO 5: Limpiando IPs externas")
        ip_count = 0
        for vm in vms:
            if getattr(vm, 'external_ip', None):
                ext_ip = vm.external_ip
                exit_code5, out5, err5 = ssh.exec(
                    f"sudo ip addr del {ext_ip}/32 dev {settings.EXTERNAL_INTERFACE} 2>/dev/null || true"
                )
                logger.info(f"🔥🔥🔥 [DESTROY]   IP {ext_ip}: exit_code={exit_code5}, err={err5}")
                ip_count += 1
        logger.info(f"🔥🔥🔥 [DESTROY]   Limpiadas {ip_count} IPs externas")
        
        logger.info(f"🔥🔥🔥 [DESTROY] ✅✅✅ Gateway {gw_name} completamente destruido en {self.worker_ip}")