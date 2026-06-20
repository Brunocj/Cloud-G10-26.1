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

    def configure_taps_batch(self, ssh: SSHClient, tap_vlan_pairs: list[tuple[str, int]]) -> None:
        """Configura N TAPs en exactamente 2 llamadas SSH independientemente de N.

        tap_vlan_pairs: lista de (tap_name, vlan_id)

        Llamada 1 — crear y levantar todos los TAPs en un solo bash:
          bash -c 'ip tuntap add tap1 || true; ip link set tap1 up; ...'

        Llamada 2 — OVS batch: add-br + N×(add-port + set tag) en un solo ovs-vsctl:
          ovs-vsctl --may-exist add-br br-int
                    -- --may-exist add-port br-int tap1 -- set port tap1 tag=100
                    -- --may-exist add-port br-int tap2 -- set port tap2 tag=200 ...
        """
        if not tap_vlan_pairs:
            return

        # 1. Crear y levantar todos los TAPs en un único shell one-liner
        tap_cmds = "; ".join(
            f"sudo ip tuntap add dev {tap} mode tap 2>/dev/null || true; sudo ip link set {tap} up || true"
            for tap, _ in tap_vlan_pairs
        )
        ssh.exec(f"bash -c '{tap_cmds}'")

        # 2. OVS: crear br-int + add-port + set tag para TODOS los TAPs en una sola llamada
        ovs_parts = ["--may-exist add-br br-int"]
        for tap, vlan in tap_vlan_pairs:
            ovs_parts.append(f"--may-exist add-port br-int {tap}")
            ovs_parts.append(f"set port {tap} tag={vlan}")
        exit_code, _, err = ssh.exec("sudo ovs-vsctl " + " -- ".join(ovs_parts))
        if exit_code != 0:
            raise RuntimeError(f"Fallo OVS batch en {self.worker_ip}: {err}")

        for tap, vlan in tap_vlan_pairs:
            logger.info(f"[{self.worker_ip}] Enlace configurado: {tap} -> VLAN {vlan}")

    def ensure_br_int(self, ssh: SSHClient) -> None:
        """Legado — el batch ya incluye add-br. Se mantiene por compatibilidad."""
        pass

    def configure_vlan_and_port(self, ssh: SSHClient, tap_interface: str, vlan_id: int) -> None:
        """Versión unitaria — usa configure_taps_batch internamente."""
        self.configure_taps_batch(ssh, [(tap_interface, vlan_id)])

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
        """Desconecta el TAP del switch virtual y lo elimina del OS durante la destrucción del slice."""
        exit_code, _, err = ssh.exec(f"sudo ovs-vsctl --if-exists del-port br-int {tap_interface}")
        if exit_code == 0:
            logger.info(f"[{self.worker_ip}] Puerto {tap_interface} eliminado de OVS")
        else:
            logger.warning(f"[{self.worker_ip}] Error borrando puerto OVS {tap_interface}: {err}")
            
        # 🔥 ELIMINAR EL TAP DEL SO (Asumimos responsabilidad total)
        exit_code_ip, _, err_ip = ssh.exec(f"sudo ip tuntap del dev {tap_interface} mode tap || true")
        if exit_code_ip == 0:
            logger.info(f"[{self.worker_ip}] TAP {tap_interface} eliminada físicamente del OS")
        else:
            logger.warning(f"[{self.worker_ip}] Error eliminando TAP {tap_interface} del OS: {err_ip}")

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

            # 🔥 3. DOBLE NAT: Permite acceso SSH desde VPN a las VMs con IP externa
            # El MASQUERADE en gw_name hace que la VM siempre responda al gateway
            # local, evitando el problema de routing asimétrico en la respuesta.
            ssh.exec(f"sudo iptables -t nat -A POSTROUTING -o {gw_name} -j MASQUERADE")
            logger.info(f"[{self.worker_ip}] Doble NAT (MASQUERADE) habilitado en {gw_name}")

            # 🔥 4. POLICY ROUTING: Respuestas de VMs con IP externa regresan por br-int al GW
            # La tabla 100 rutea el tráfico destinado a redes externas (VPN) via br-int.
            # Usamos bash -c para que || true funcione (paramiko no invoca shell por defecto).
            gw_ip_prefix = settings.EXTERNAL_POOL_CIDR.split('/')[0].rsplit('.', 1)[0]
            ssh.exec(f"bash -c 'sudo ip rule add iif {gw_name} table 100 priority 100 || true'")
            ssh.exec(f"bash -c 'sudo ip route replace 10.8.0.0/24 via {gw_ip_prefix}.1 dev {settings.EXTERNAL_INTERFACE} table 100 || true'")
            logger.info(f"[{self.worker_ip}] Policy routing tabla 100 configurado para {gw_name}")
            
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
                        f"sudo iptables -t nat -A PREROUTING "
                        f"-d {ext_ip} -j DNAT --to-destination {internal_vm_ip}"
                    )
                    ssh.exec(cmd_dnat)

                    # Forward explícito para permitir el flujo de entrada/salida
                    ssh.exec(
                        f"sudo iptables -I FORWARD 1 "
                        f"-d {internal_vm_ip}/32 -j ACCEPT"
                    )
                    ssh.exec(
                        f"sudo iptables -I FORWARD 1 "
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

        # 🔥 Limpiar Doble NAT (MASQUERADE en gw_name) y policy routing
        ssh.exec(f"sudo iptables -t nat -D POSTROUTING -o {gw_name} -j MASQUERADE || true")
        ssh.exec(f"sudo ip rule del iif {gw_name} table 100 priority 100 || true")
        logger.info(f"🔥🔥🔥 [DESTROY]   Doble NAT y policy routing limpiados para {gw_name}")
        
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
                        f"sudo iptables -t nat -D PREROUTING "
                        f"-d {ext_ip} -j DNAT --to-destination {internal_vm_ip} || true"
                    )
                    exit_code_dnat, _, err_dnat = ssh.exec(cmd_dnat_del)
                    logger.info(f"🔥🔥🔥 [DESTROY]   DNAT para {ext_ip}: exit_code={exit_code_dnat}, err={err_dnat}")

                    # Remover reglas de forward para acceso exterior
                    ssh.exec(
                        f"sudo iptables -D FORWARD "
                        f"-d {internal_vm_ip}/32 -j ACCEPT || true"
                    )
                    ssh.exec(
                        f"sudo iptables -D FORWARD "
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