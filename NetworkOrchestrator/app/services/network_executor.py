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

        mtu = getattr(settings, "DATA_TRUNK_MTU", 0) or 0

        # 1. Crear y levantar todos los TAPs en un único shell one-liner
        tap_cmds = "; ".join(
            f"sudo ip tuntap add dev {tap} mode tap 2>/dev/null || true; "
            f"sudo ip link set {tap} up || true"
            + (f"; sudo ip link set {tap} mtu {mtu} || true" if mtu else "")
            for tap, _ in tap_vlan_pairs
        )
        ssh.exec(f"bash -c '{tap_cmds}'")

        # 2. OVS: crear br-int + add-port + set tag para TODOS los TAPs en una sola llamada
        ovs_parts = ["--may-exist add-br br-int"]
        # Trunk de datos inter-worker: re-colgar ens4 a br-int (idempotente). Sin
        # esto, los enlaces p2p entre VMs de workers distintos NO cruzan (el trunk
        # se pierde al reiniciar/migrar la infra). ens4 no tiene IP → seguro.
        trunk = getattr(settings, "DATA_TRUNK_IFACE", "") or ""
        if trunk:
            ovs_parts.append(f"--may-exist add-port br-int {trunk}")
        for tap, vlan in tap_vlan_pairs:
            ovs_parts.append(f"--may-exist add-port br-int {tap}")
            ovs_parts.append(f"set port {tap} tag={vlan}")

        ovs_cmd = "sudo ovs-vsctl " + " -- ".join(ovs_parts)
        # br-int y el trunk deben quedar al MISMO mtu reducido que las taps, o la
        # interfaz "miente" con 1500 mientras el camino físico real hacia otro
        # worker no lo soporta (blackhole de PMTU: sin ICMP "frag needed" de
        # vuelta, TCP nunca se entera y se cuelga en el primer paquete grande —
        # ver DATA_TRUNK_MTU). Encadenado en el MISMO comando (RC capturado antes)
        # para no sumar una 3ª llamada SSH ni alterar el exit code que valida el
        # ovs-vsctl de abajo.
        if mtu:
            full_cmd = (
                f"bash -c '{ovs_cmd}; RC=$?; "
                f"sudo ip link set br-int mtu {mtu} 2>/dev/null || true"
                + (f"; sudo ip link set {trunk} mtu {mtu} 2>/dev/null || true" if trunk else "")
                + "; exit $RC'"
            )
        else:
            full_cmd = ovs_cmd

        exit_code, _, err = ssh.exec(full_cmd)
        if exit_code != 0:
            raise RuntimeError(f"Fallo OVS batch en {self.worker_ip}: {err}")

        for tap, vlan in tap_vlan_pairs:
            logger.info(f"[{self.worker_ip}] Enlace configurado: {tap} -> VLAN {vlan}")

    def setup_qinq_trunk(self, ssh: SSHClient, s_vlan: int, c_vlans: list[int], trunk_iface: str = "ens4") -> None:
        """
        Configura Q-in-Q (802.1ad) para un slice sobre el trunk inter-worker.

        Modelo (probado en el cluster):
          tap (access C-VID) → br-int → [patch] → br-qinq (dot1q-tunnel +S-VID) → ens4
        - `vlan-limit=2`: OVS debe parsear 2 tags o el ingress no desencapsula.
        - `br-qinq` es el bridge provider; `ens4` se mueve ahí (sale de br-int).
        - Un patch por slice: en br-int troncaliza los C-VIDs del slice; en
          br-qinq es dot1q-tunnel que empuja el S-VID (0x88a8) sobre ellos.
        Idempotente: usa --may-exist y `add` (append) para soportar Modo Edición.
        """
        cvlans_sp = " ".join(str(v) for v in sorted(set(c_vlans)))
        pi = f"pi-{s_vlan}"   # patch en br-int
        pq = f"pq-{s_vlan}"   # patch en br-qinq
        cmds = [
            "ovs-vsctl set Open_vSwitch . other_config:vlan-limit=2",
            "ovs-vsctl --may-exist add-br br-qinq",
            f"ovs-vsctl --if-exists del-port br-int {trunk_iface}",
            f"ovs-vsctl --may-exist add-port br-qinq {trunk_iface}",
            f"ovs-vsctl --may-exist add-port br-int {pi} -- set interface {pi} type=patch options:peer={pq}",
            f"ovs-vsctl --may-exist add-port br-qinq {pq} -- set interface {pq} type=patch options:peer={pi}",
            f"ovs-vsctl set port {pq} vlan_mode=dot1q-tunnel tag={s_vlan} other_config:qinq-ethtype=802.1ad",
        ]
        if cvlans_sp:
            cmds.append(f"ovs-vsctl add port {pi} trunks {cvlans_sp}")
            cmds.append(f"ovs-vsctl add port {pq} cvlans {cvlans_sp}")
        full = " && ".join(f"sudo {c}" for c in cmds)
        exit_code, _, err = ssh.exec(full)
        if exit_code != 0:
            raise RuntimeError(f"Fallo Q-in-Q en {self.worker_ip}: {err}")
        logger.info(f"[{self.worker_ip}] 🏷️  Q-in-Q OK: S-VID {s_vlan} sobre C-VIDs [{cvlans_sp}]")

    def teardown_qinq_slice(self, ssh: SSHClient, s_vlan: int) -> None:
        """Elimina el patch dot1q-tunnel de un slice (destroy). No toca br-qinq
        ni ens4 (infra compartida entre slices Q-in-Q)."""
        pi = f"pi-{s_vlan}"; pq = f"pq-{s_vlan}"
        ssh.exec(f"sudo ovs-vsctl --if-exists del-port br-int {pi}; "
                 f"sudo ovs-vsctl --if-exists del-port br-qinq {pq}")
        logger.info(f"[{self.worker_ip}] Q-in-Q: patch del slice S-VID {s_vlan} eliminado")

    def ensure_br_int(self, ssh: SSHClient) -> None:
        """Legado — el batch ya incluye add-br. Se mantiene por compatibilidad."""
        pass

    def configure_vlan_and_port(self, ssh: SSHClient, tap_interface: str, vlan_id: int) -> None:
        """Versión unitaria — usa configure_taps_batch internamente."""
        self.configure_taps_batch(ssh, [(tap_interface, vlan_id)])

    def apply_security_groups(self, ssh: SSHClient, tap_interface: str, rules: list[SecurityRule]) -> None:
        """Aplica firewall deny-by-default por TAP usando iptables + conntrack.

        Semántica (R5 / prueba 5.4.1 NETWORK_SECURITY_RULES):
          - Solo se aceptan los (protocolo, puerto) declarados por el usuario
            en el sentido de INGRESO a la VM (--physdev-out {tap}).
          - Todo lo demás que entre a la VM se DROPPEA.
          - Se permite tráfico de retorno (ESTABLISHED,RELATED) para que las
            conexiones que la propia VM inicia sigan funcionando.
          - Idempotente: se limpian reglas previas del mismo TAP antes de
            reinsertarlas (permite re-deploy sin acumular).
        """
        # 1) Limpieza idempotente: borrar TODA regla FORWARD previa que apunte a
        #    este TAP (tanto --physdev-out como --physdev-in). Se hace vía awk
        #    sobre `iptables -S` para no depender de conocer las reglas exactas.
        cleanup = (
            f"sudo iptables -S FORWARD | "
            f"grep -E -- '--physdev-(in|out) {tap_interface}( |$)' | "
            f"sed 's/^-A /-D /' | "
            f"while read r; do sudo iptables $r || true; done"
        )
        ssh.exec(f"bash -c \"{cleanup}\"")

        # Si no hay reglas de usuario, no instalamos firewall (VM totalmente
        # abierta a nivel L3, mismo comportamiento previo).
        if not rules:
            logger.debug(f"[{self.worker_ip}] Sin reglas de seguridad para {tap_interface} — firewall abierto")
            return

        # 2) DROP por defecto al final de FORWARD para tráfico HACIA la VM.
        #    Se hace con -A (append) para que quede DESPUÉS de los ACCEPTs
        #    que insertaremos con -I (top).
        drop_cmd = (
            f"sudo iptables -A FORWARD "
            f"-m physdev --physdev-out {tap_interface} -j DROP"
        )
        ssh.exec(drop_cmd)

        # 3) ACCEPT de conntrack para respuestas de conexiones que la VM inició.
        #    Insertado en pos 1 para evaluarse antes del DROP.
        ct_cmd = (
            f"sudo iptables -I FORWARD 1 "
            f"-m physdev --physdev-out {tap_interface} "
            f"-m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT"
        )
        ssh.exec(ct_cmd)

        # 4) ACCEPT explícito por cada regla del usuario (INGRESO a la VM).
        for rule in rules:
            cmd = (
                f"sudo iptables -I FORWARD 1 "
                f"-m physdev --physdev-out {tap_interface} "
                f"-p {rule.protocol} --dport {rule.allow_port} -j ACCEPT"
            )
            exit_code, _, err = ssh.exec(cmd)
            if exit_code != 0:
                logger.warning(f"[{self.worker_ip}] Fallo al aplicar firewall {rule}: {err}")
            else:
                logger.info(f"[{self.worker_ip}] FW ACCEPT {rule.protocol}/{rule.allow_port} -> {tap_interface}")

        logger.info(
            f"[{self.worker_ip}] Firewall deny-by-default aplicado en {tap_interface} "
            f"({len(rules)} regla(s) permitida(s))"
        )

    def destroy_port(self, ssh: SSHClient, tap_interface: str) -> None:
        """Desconecta el TAP del switch virtual y lo elimina del OS durante la destrucción del slice."""
        # Limpiar reglas de firewall (SecurityRule) aplicadas por apply_security_groups
        # sobre este TAP, para no dejar reglas huérfanas en el kernel al recrear.
        cleanup_fw = (
            f"sudo iptables -S FORWARD | "
            f"grep -E -- '--physdev-(in|out) {tap_interface}( |$)' | "
            f"sed 's/^-A /-D /' | "
            f"while read r; do sudo iptables $r || true; done"
        )
        ssh.exec(f"bash -c \"{cleanup_fw}\"")

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
            # 0. Aislamiento entre slices: bloquear el forwarding IP entre
            #    CUALQUIER PAR de gateways gw_* de este worker.
            #
            #    Cada gw_XXX vive en el namespace de red por defecto del host
            #    (no hay `ip netns`), y net.ipv4.ip_forward se activa GLOBAL
            #    (no por interfaz). Cuando dos o más slices tienen VMs en el
            #    mismo worker, sus subredes (10.0.<slice>.0/24) quedan todas
            #    directamente conectadas en la tabla de ruteo del kernel — sin
            #    esta regla, el propio worker enruta tráfico de un slice hacia
            #    otro por IP, sin pasar nunca por el aislamiento de VLAN de
            #    OVS (que es por donde SÍ se filtra correctamente el tráfico
            #    L2 entre slices que NO comparten worker).
            #
            #    `-i gw_+ -o gw_+` solo hace match cuando AMBAS interfaces son
            #    gateways (gw_123, gw_124, ...) — el tráfico WAN (SNAT/egress)
            #    y el DNAT de IPs externas usan como interfaz al otro lado
            #    WAN_INTERFACE/EXTERNAL_INTERFACE, así que no se ven afectados.
            #    Se instala una sola vez por worker (idempotente vía -C).
            check_cmd = "sudo iptables -C FORWARD -i gw_+ -o gw_+ -j DROP"
            exit_code, _, _ = ssh.exec(check_cmd)
            if exit_code != 0:
                ssh.exec("sudo iptables -I FORWARD 1 -i gw_+ -o gw_+ -j DROP")
                logger.info(f"[{self.worker_ip}] 🔒 Regla de aislamiento inter-slice instalada (gw_* ⇄ gw_*)")

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

                    # Respuestas de conexiones ya establecidas (en cualquier sentido)
                    # siempre pasan — necesario tanto para lo que la VM inicia como
                    # para las conexiones entrantes que sí matchean una regla abajo.
                    ssh.exec(
                        f"sudo iptables -I FORWARD 1 "
                        f"-d {internal_vm_ip}/32 -m state --state ESTABLISHED,RELATED -j ACCEPT"
                    )
                    ssh.exec(
                        f"sudo iptables -I FORWARD 1 "
                        f"-s {internal_vm_ip}/32 -m state --state ESTABLISHED,RELATED -j ACCEPT"
                    )

                    # Ingreso desde Internet (AWS-style, deny-by-default): solo lo
                    # que el usuario declaró en ingress_rules entra por la IP
                    # externa/VPN — el resto se DROPPEA. Antes esto era un ACCEPT
                    # incondicional (`-d {ip} -j ACCEPT`), full-open sin importar
                    # el Firewall Interno (que ni siquiera corre sobre este camino,
                    # solo ve TAPs de enlace).
                    #
                    # OJO: NO se scopea con `-i {EXTERNAL_INTERFACE}` (br-int) — se
                    # probó en cluster real y ese match nunca hace hit (contador en
                    # 0 con tráfico real cruzando la regla): con OVS, el paquete
                    # DNAT'eado hacia la subred del slice no llega a FORWARD con
                    # `-i br-int` reportado por netfilter (el datapath de OVS no
                    # expone la interfaz "puente" ahí como lo haría un bridge Linux
                    # normal), así que cae a la policy ACCEPT por defecto del chain
                    # y el filtro queda de adorno. Se matchea solo por destino, igual
                    # que el ACCEPT incondicional original — el tráfico intra-slice
                    # sigue sin verse afectado porque nunca pasa por FORWARD (se
                    # conmuta a nivel L2/OVS, no se rutea).
                    ingress_rules = getattr(vm, 'ingress_rules', []) or []
                    for rule in ingress_rules:
                        proto = getattr(rule, 'protocol', 'tcp')
                        if proto == "icmp":
                            cmd = f"sudo iptables -I FORWARD 1 -d {internal_vm_ip}/32 -p icmp -j ACCEPT"
                        else:
                            cmd = (
                                f"sudo iptables -I FORWARD 1 "
                                f"-d {internal_vm_ip}/32 -p {proto} --dport {rule.allow_port} -j ACCEPT"
                            )
                        ssh.exec(cmd)
                        logger.info(f"[{self.worker_ip}] Ingress ACCEPT {proto}/{getattr(rule, 'allow_port', '-')} -> {internal_vm_ip}")

                    ssh.exec(f"sudo iptables -A FORWARD -d {internal_vm_ip}/32 -j DROP")

                    logger.info(
                        f"[{self.worker_ip}] DNAT exterior habilitado ({settings.EXTERNAL_INTERFACE}): "
                        f"{ext_ip} -> {internal_vm_ip} ({len(ingress_rules)} regla(s) de entrada)"
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

                    # Remover reglas de forward para acceso exterior (ESTABLISHED,RELATED
                    # en ambos sentidos — dst-based reemplazó al ACCEPT incondicional legado)
                    ssh.exec(
                        f"sudo iptables -D FORWARD "
                        f"-d {internal_vm_ip}/32 -m state --state ESTABLISHED,RELATED -j ACCEPT || true"
                    )
                    ssh.exec(
                        f"sudo iptables -D FORWARD "
                        f"-s {internal_vm_ip}/32 -m state --state ESTABLISHED,RELATED -j ACCEPT || true"
                    )
                    # Limpieza genérica de las reglas de ingreso (un ACCEPT por
                    # ingress_rule + el DROP final) — por grep en vez de rearmar
                    # cada regla 1:1, así no depende de que el payload de destroy
                    # traiga las mismas ingress_rules que trajo el deploy. El
                    # patrón `-d {ip}/32 -p ` / `-d {ip}/32 -j DROP` identifica
                    # SOLO estas reglas (sin `-i`, ver comentario en deploy) sin
                    # tocar las de REGLA A (que usan `-i/-o ens3`) ni las de
                    # ESTABLISHED,RELATED (ya borradas arriba explícitamente).
                    cleanup_ingress = (
                        f"sudo iptables -S FORWARD | "
                        f"grep -E -- '-d {internal_vm_ip}/32 (-p |-j DROP)' | "
                        f"sed 's/^-A /-D /' | "
                        f"while read r; do sudo iptables $r || true; done"
                    )
                    ssh.exec(f"bash -c \"{cleanup_ingress}\"")
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