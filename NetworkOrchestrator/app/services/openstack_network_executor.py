import os
import logging
import asyncio
import ipaddress
import openstack
from app.core.config import settings
from app.services.ssh_client import SSHClient
from app.models.schemas import (
    DeployNetworkRequest, DeployNetworkResponse, DestroyNetworkRequest, DestroyNetworkResponse,
    LinkResult, ProvisioningStatus
)

logger = logging.getLogger("network-orchestrator.openstack")


def _resolve_link_subnet(vlan_id, ip1_raw, ip2_raw):
    """
    Decide el CIDR de la subnet del enlace y el fixed_ip de cada puerto según
    las IPs manuales que el usuario haya definido en el NodeEditor.

    · Ningún extremo con IP  → comportamiento ACTUAL intacto: subnet
      `10.{..}.{..}.0/30` con puertos .1 y .2 (cero regresión).
    · Al menos un extremo con IP → la subnet se deriva de esa IP respetando su
      prefijo (si no trae prefijo se asume /24). Cada puerto usa su IP de
      usuario si cae dentro del CIDR; si está vacío o pertenece a otra subred,
      Neutron le asigna una libre del mismo CIDR — Nova exige un fixed_ip en
      todo puerto adjuntado al boot, así que nunca se deja sin IP.

    En OpenStack la IP la aplica Nova al guest vía config-drive/network-config
    (match por MAC), de modo que fijar el fixed_ip del puerto ES el mecanismo
    para que la VM arranque con esa IP — sin tocar el Compute Provisioner.

    Devuelve: (cidr:str, fixed1:str|None, fixed2:str|None)
      fixedN None → Neutron auto-asigna desde la subnet.
    """
    default_cidr = f"10.{(vlan_id // 256) % 256}.{vlan_id % 256}.0/30"

    def _norm(raw):
        raw = (raw or "").strip()
        if not raw:
            return None
        if "/" not in raw:
            raw = f"{raw}/24"   # sin prefijo → /24 por defecto
        try:
            return ipaddress.ip_interface(raw)   # valida IP + prefijo juntos
        except ValueError:
            logger.warning("[OpenStack] IP de enlace inválida '%s' (VLAN %s) — ignorada", raw, vlan_id)
            return None

    if1 = _norm(ip1_raw)
    if2 = _norm(ip2_raw)

    # Ningún extremo con IP válida → comportamiento legado exacto
    if not if1 and not if2:
        base = f"10.{(vlan_id // 256) % 256}.{vlan_id % 256}"
        return default_cidr, f"{base}.1", f"{base}.2"

    # Subnet derivada del primer extremo con IP (prioriza VM1)
    network = (if1 or if2).network
    cidr = str(network)

    def _fixed(iface):
        if iface and iface.ip in network:
            return str(iface.ip)
        return None   # vacío o subred incompatible → auto-asignación de Neutron

    fixed1, fixed2 = _fixed(if1), _fixed(if2)
    if if2 and fixed2 is None and if2.ip not in network:
        logger.warning("[OpenStack] IP de VM2 %s fuera de la subred %s (VLAN %s) — auto-asignada",
                        if2.ip, cidr, vlan_id)
    return cidr, fixed1, fixed2


def get_connection():
    """Establece conexión con la API de OpenStack usando openstacksdk."""
    return openstack.connect(
        auth_url=os.getenv("OS_AUTH_URL"),
        username=os.getenv("OS_USERNAME", "admin"),
        password=os.getenv("OS_PASSWORD", ""),
        project_name=os.getenv("OS_PROJECT_NAME", "admin"),
        user_domain_name=os.getenv("OS_USER_DOMAIN_NAME", "Default"),
        project_domain_name=os.getenv("OS_PROJECT_DOMAIN_NAME", "Default"),
    )


# ── Q-in-Q (802.1ad) para OpenStack ────────────────────────────────────────────
# Se interpone un br-qinq con un puerto dot1q-tunnel que empuja el S-VID del
# slice sobre los C-VIDs (segmentation_ids que Neutron asigna a cada red de
# enlace). Se aplica en el paso de RED (pre-compute), en cada compute donde el
# placement (host_map) colocará una VM del slice — igual que en Linux Cluster.

def _qinq_apply_cmd(s_vlan: int, cvlans_sp: str) -> str:
    """Secuencia OVS (validada a mano) para interponer Q-in-Q en un compute."""
    pi, pq = f"pi-{s_vlan}", f"pq-{s_vlan}"
    cmds = [
        "ovs-vsctl set Open_vSwitch . other_config:vlan-limit=2",
        "ovs-vsctl --may-exist add-br br-qinq",
        "ovs-vsctl set-fail-mode br-qinq standalone",
        "ovs-vsctl --if-exists del-port br-vlan ens4",
        "ovs-vsctl --may-exist add-port br-qinq ens4",
        f"ovs-vsctl --may-exist add-port br-vlan {pi} -- set interface {pi} type=patch options:peer={pq}",
        f"ovs-vsctl --may-exist add-port br-qinq {pq} -- set interface {pq} type=patch options:peer={pi}",
        f"ovs-vsctl set port {pq} vlan_mode=dot1q-tunnel tag={s_vlan} other_config:qinq-ethtype=802.1ad",
    ]
    if cvlans_sp:
        cmds.append(f"ovs-vsctl add port {pq} cvlans {cvlans_sp}")
    return " && ".join(f"sudo {c}" for c in cmds)


def _qinq_teardown_cmd(s_vlan: int) -> str:
    """
    Quita el patch dot1q-tunnel de ESTE slice. Si era el último slice con
    Q-in-Q en el compute (no quedan patches pq-*), devuelve ens4 a br-vlan y
    elimina br-qinq para restaurar el estado normal del compute.
    """
    pi, pq = f"pi-{s_vlan}", f"pq-{s_vlan}"
    return (
        f"sudo ovs-vsctl --if-exists del-port br-vlan {pi}; "
        f"sudo ovs-vsctl --if-exists del-port br-qinq {pq}; "
        f"if [ \"$(sudo ovs-vsctl list-ports br-qinq 2>/dev/null | grep -c '^pq-')\" = \"0\" ]; then "
        f"sudo ovs-vsctl --if-exists del-port br-qinq ens4; "
        f"sudo ovs-vsctl --may-exist add-port br-vlan ens4; "
        f"sudo ovs-vsctl --if-exists del-br br-qinq; fi"
    )


def _ssh_run(creds: dict, cmd: str):
    with SSHClient(creds["ip"], creds["user"], creds.get("key", ""),
                   port=int(creds.get("port", 22))) as ssh:
        return ssh.exec(cmd)


async def _qinq_apply(s_vlan: int, host_cvlans: dict, compute_ssh_map: dict) -> None:
    """host_cvlans: {host_de_nova: set(C-VIDs)}. SSHea a cada compute y aplica el tunnel."""
    if not s_vlan or not host_cvlans or not compute_ssh_map:
        return
    for host, cvlans in host_cvlans.items():
        creds = compute_ssh_map.get(host)
        if not creds:
            logger.error(f"[OpenStack][QinQ] Sin SSH para compute '{host}' — omitido "
                         f"(¿Worker.name == host de Nova?)")
            continue
        cvlans_sp = " ".join(str(v) for v in sorted(cvlans))
        cmd = _qinq_apply_cmd(s_vlan, cvlans_sp)
        try:
            ec, _, err = await asyncio.to_thread(_ssh_run, creds, cmd)
            if ec != 0:
                logger.error(f"[OpenStack][QinQ] Fallo en compute '{host}': {err}")
            else:
                logger.info(f"[OpenStack][QinQ] ✅ compute '{host}': S-VID {s_vlan} sobre C-VIDs [{cvlans_sp}]")
        except Exception as exc:
            logger.error(f"[OpenStack][QinQ] Error SSH a '{host}': {exc}")


async def _qinq_teardown(s_vlan: int, compute_ssh_map: dict) -> None:
    if not s_vlan or not compute_ssh_map:
        return
    cmd = _qinq_teardown_cmd(s_vlan)
    for host, creds in compute_ssh_map.items():
        try:
            await asyncio.to_thread(_ssh_run, creds, cmd)
            logger.info(f"[OpenStack][QinQ] teardown en compute '{host}' (S-VID {s_vlan})")
        except Exception as exc:
            logger.warning(f"[OpenStack][QinQ] teardown en '{host}' falló: {exc}")


# ── Purga idempotente de recursos de red de un slice ───────────────────────────
# Borra por ID TODOS los recursos que coincidan por nombre (incluye duplicados
# de intentos fallidos previos). Se usa como GUARDIA pre-deploy (clean slate) y
# durante el destroy, para que nunca se acumulen recursos huérfanos.

async def _purge_slice_networking(conn, slice_id: str, link_vlans: list) -> None:
    def _list(gen_call):
        return list(gen_call())

    router_name = f"router-slice-{slice_id}"
    net_names   = [f"net-slice-{slice_id}"] + [f"net-link-{v}" for v in (link_vlans or [])]
    sg_names    = [f"secgroup-slice-{slice_id}", f"secgroup-slice-{slice_id}-no-internet"]

    # 1. Routers (todos los homónimos): quitar interfaces + gateway, borrar por ID
    routers = await asyncio.to_thread(_list, lambda: conn.network.routers(name=router_name))
    for r in routers:
        iface_ports = await asyncio.to_thread(
            _list, lambda rid=r.id: conn.network.ports(device_id=rid))
        for p in iface_ports:
            try:
                await asyncio.to_thread(conn.network.remove_interface_from_router, r.id, port_id=p.id)
            except Exception:
                pass
        try:
            await asyncio.to_thread(conn.network.update_router, r, external_gateway_info=None)
        except Exception:
            pass
        try:
            await asyncio.to_thread(conn.network.delete_router, r.id)
            logger.info(f"[OpenStack][PURGE] router {router_name} ({r.id}) eliminado")
        except Exception as e:
            logger.warning(f"[OpenStack][PURGE] no se pudo borrar router {r.id}: {e}")

    # 2. Redes (todas las homónimas): liberar FIPs + borrar puertos, luego la red
    for nm in net_names:
        nets = await asyncio.to_thread(_list, lambda name=nm: conn.network.networks(name=name))
        for net in nets:
            ports = await asyncio.to_thread(
                _list, lambda nid=net.id: conn.network.ports(network_id=nid))
            for p in ports:
                fips = await asyncio.to_thread(
                    _list, lambda pid=p.id: conn.network.ips(port_id=pid))
                for fip in fips:
                    try:
                        await asyncio.to_thread(conn.network.delete_ip, fip.id)
                    except Exception:
                        pass
                try:
                    await asyncio.to_thread(conn.network.delete_port, p.id)
                except Exception:
                    pass
            try:
                await asyncio.to_thread(conn.network.delete_network, net.id)
                logger.info(f"[OpenStack][PURGE] red {nm} ({net.id}) eliminada")
            except Exception as e:
                logger.warning(f"[OpenStack][PURGE] no se pudo borrar red {net.id}: {e}")

    # 3. Security groups (todos los homónimos)
    for nm in sg_names:
        sgs = await asyncio.to_thread(_list, lambda name=nm: conn.network.security_groups(name=name))
        for sg in sgs:
            try:
                await asyncio.to_thread(conn.network.delete_security_group, sg.id)
                logger.info(f"[OpenStack][PURGE] security group {nm} ({sg.id}) eliminado")
            except Exception as e:
                logger.warning(f"[OpenStack][PURGE] no se pudo borrar SG {sg.id}: {e}")

    # 3.b Security groups por VM (secgroup-slice-{slice}-vm-*): se listan todos
    # y se filtran por prefijo, ya que los vm_id no se conocen a priori en purge.
    sg_vm_prefix = f"secgroup-slice-{slice_id}-vm-"
    all_sgs = await asyncio.to_thread(_list, lambda: conn.network.security_groups())
    for sg in all_sgs:
        if getattr(sg, "name", "").startswith(sg_vm_prefix):
            try:
                await asyncio.to_thread(conn.network.delete_security_group, sg.id)
                logger.info(f"[OpenStack][PURGE] SG por VM {sg.name} ({sg.id}) eliminado")
            except Exception as e:
                logger.warning(f"[OpenStack][PURGE] no se pudo borrar SG por VM {sg.id}: {e}")

class OpenStackNetworkExecutor:
    """Implementa el aprovisionamiento de red para la zona OpenStack (Strategy Pattern)."""

    async def deploy(self, request: DeployNetworkRequest) -> DeployNetworkResponse:
        slice_id = request.slice_id
        logger.info(f"[OpenStack] Iniciando deploy de red para slice {slice_id}")

        network_name   = f"net-slice-{slice_id}"
        subnet_name    = f"subnet-slice-{slice_id}"
        secgroup_name  = f"secgroup-slice-{slice_id}"
        router_name    = f"router-slice-{slice_id}"

        # Track created resources for rollback on partial failure
        _created_network   = None
        _created_subnet    = None
        _created_sec_group = None
        _created_sec_group_no_internet = None
        _created_router    = None
        _created_ports     = []
        _created_link_networks = []
        _created_link_subnets = []
        # SGs por VM creados a partir de las reglas del usuario (R5 / prueba 5.4.1).
        # Se registran para rollback y luego se limpian en destroy vía prefijo.
        _created_sec_group_per_vm = []

        async def _rollback(conn):
            """Best-effort cleanup of resources created so far.
            En modo extend, la red/subnet/SGs/router son PRE-EXISTENTES del
            slice activo — solo se limpian puertos y redes de enlace nuevos."""
            logger.warning(f"[OpenStack] Iniciando rollback de recursos de red para slice {slice_id}")
            for port in reversed(_created_ports):
                try:
                    fips = list(await asyncio.to_thread(conn.network.ips, port_id=port.id))
                    for fip in fips:
                        await asyncio.to_thread(conn.network.delete_ip, fip.id)
                    await asyncio.to_thread(conn.network.delete_port, port.id)
                except Exception as e:
                    logger.warning(f"[OpenStack] Rollback: error al borrar puerto {port.id}: {e}")
            for subnet_id in reversed(_created_link_subnets):
                try:
                    await asyncio.to_thread(conn.network.delete_subnet, subnet_id)
                except Exception:
                    pass
            for net_id in reversed(_created_link_networks):
                try:
                    await asyncio.to_thread(conn.network.delete_network, net_id)
                except Exception:
                    pass
            if is_extend:
                return   # lo demás pertenece al slice activo — NO tocar
            if _created_router:
                try:
                    if _created_subnet:
                        await asyncio.to_thread(conn.network.remove_interface_from_router, _created_router.id, subnet_id=_created_subnet.id)
                except Exception:
                    pass
                try:
                    await asyncio.to_thread(conn.network.delete_router, _created_router.id)
                except Exception as e:
                    logger.warning(f"[OpenStack] Rollback: error al borrar router: {e}")
            if _created_subnet:
                try:
                    await asyncio.to_thread(conn.network.delete_subnet, _created_subnet.id)
                except Exception as e:
                    logger.warning(f"[OpenStack] Rollback: error al borrar subnet: {e}")
            if _created_network:
                try:
                    await asyncio.to_thread(conn.network.delete_network, _created_network.id)
                except Exception as e:
                    logger.warning(f"[OpenStack] Rollback: error al borrar red: {e}")
            if _created_sec_group:
                try:
                    await asyncio.to_thread(conn.network.delete_security_group, _created_sec_group.id)
                except Exception as e:
                    logger.warning(f"[OpenStack] Rollback: error al borrar security group: {e}")
            if _created_sec_group_no_internet:
                try:
                    await asyncio.to_thread(conn.network.delete_security_group, _created_sec_group_no_internet.id)
                except Exception as e:
                    logger.warning(f"[OpenStack] Rollback: error al borrar security group no-internet: {e}")
            # SGs por VM (reglas del usuario)
            for sg in _created_sec_group_per_vm:
                try:
                    await asyncio.to_thread(conn.network.delete_security_group, sg.id)
                except Exception as e:
                    logger.warning(f"[OpenStack] Rollback: error al borrar SG por VM {sg.name}: {e}")

        is_extend = getattr(request, "mode", "deploy") == "extend"

        try:
            conn = await asyncio.to_thread(get_connection)

            # ── MODO EXTEND (REQ-US-14): reutilizar la infraestructura del slice ──
            # La red/subnet/secgroups/router YA existen; solo se crean los puertos
            # de las VMs nuevas y las redes de los enlaces nuevos.
            if is_extend:
                _created_network = await asyncio.to_thread(conn.network.find_network, network_name)
                if not _created_network:
                    raise RuntimeError(f"Extend: la red del slice '{network_name}' no existe.")
                _created_subnet    = await asyncio.to_thread(conn.network.find_subnet, subnet_name)
                _created_sec_group = await asyncio.to_thread(conn.network.find_security_group, secgroup_name)
                _created_sec_group_no_internet = await asyncio.to_thread(
                    conn.network.find_security_group, f"secgroup-slice-{slice_id}-no-internet")
                logger.info(f"[OpenStack][EXTEND] Reutilizando red existente {network_name} "
                            f"({_created_network.id}) para la extensión del slice {slice_id}")

            if not is_extend:
                # 0. GUARDIA idempotente: purgar cualquier resto previo de este
                # slice_id (de un intento fallido anterior) para NO acumular
                # recursos duplicados con el mismo nombre en un re-deploy.
                try:
                    await _purge_slice_networking(
                        conn, slice_id, [l.vlan_id for l in request.links])
                except Exception as _pexc:
                    logger.warning(f"[OpenStack] Guardia pre-deploy: purga best-effort falló: {_pexc}")

                # 1. Crear Provider Network
                network_args = {"name": network_name}
                if settings.OS_NETWORK_MTU:
                    network_args["mtu"] = settings.OS_NETWORK_MTU
                if os.getenv("OS_VLAN_TRANSPARENT", "").lower() in ("1", "true", "yes"):
                    network_args["vlan_transparent"] = True
                prov_type = os.getenv("OS_PROVIDER_NETWORK_TYPE")
                if prov_type:
                    network_args["provider_network_type"] = prov_type
                prov_phys = os.getenv("OS_PROVIDER_PHYSICAL_NETWORK")
                if prov_phys:
                    network_args["provider_physical_network"] = prov_phys
                # segmentation_id: se FUERZA al mgmt_vlan reservado por el
                # SliceManager (opción 1 → la tabla `vlans` = el tag real de
                # Neutron). Si no viene o falta physnet, cae al env legado.
                _mgmt_vlan = getattr(request, "mgmt_vlan", None)
                if _mgmt_vlan and prov_phys:
                    network_args["provider_network_type"] = "vlan"
                    network_args["provider_segmentation_id"] = int(_mgmt_vlan)
                elif os.getenv("OS_PROVIDER_SEGMENTATION_ID"):
                    network_args["provider_segmentation_id"] = int(os.getenv("OS_PROVIDER_SEGMENTATION_ID"))

                _created_network = await asyncio.to_thread(conn.network.create_network, **network_args)
                logger.info(f"[OpenStack] net-slice creada con segmentation_id={getattr(_created_network,'provider_segmentation_id',None)} "
                            f"(mgmt_vlan solicitado={_mgmt_vlan})")
                logger.info(f"[OpenStack] Red Provider creada: {_created_network.name} (ID: {_created_network.id}, "
                            f"MTU solicitada={settings.OS_NETWORK_MTU or 'default'}, "
                            f"MTU efectiva={getattr(_created_network, 'mtu', '?')})")

                # 2. Crear Subnet asociada con CIDR dinámico
                if request.vms:
                    first_ip = request.vms[0].internal_ip
                    parts = first_ip.split(".")
                    cidr = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
                    gateway_ip = f"{parts[0]}.{parts[1]}.{parts[2]}.1"
                else:
                    cidr = "10.0.1.0/24"
                    gateway_ip = "10.0.1.1"

                # DNS de la subnet: sin esto la VM queda sin resolver (ver nota
                # en Settings.OS_SUBNET_DNS_NAMESERVERS). Nova lo propaga a la
                # VM por el network_data.json del config-drive.
                _dns = [d.strip() for d in settings.OS_SUBNET_DNS_NAMESERVERS.split(",") if d.strip()]

                _created_subnet = await asyncio.to_thread(
                    conn.network.create_subnet,
                    name=subnet_name,
                    network_id=_created_network.id,
                    ip_version=4,
                    cidr=cidr,
                    gateway_ip=gateway_ip,
                    dns_nameservers=_dns,
                    # DHCP no es alcanzable en este cluster (mismo problema que ya
                    # tuvimos con el servicio de metadata) — con DHCP habilitado,
                    # cloud-init confía en que la VM va a pedir la IP por DHCP en
                    # vez de tomarla estática desde network_data.json (config-drive),
                    # y como nunca llega respuesta, la interfaz de gestión queda sin
                    # IP. Deshabilitarlo fuerza el fixed_ip del puerto como estático.
                    enable_dhcp=False,
                )
                logger.info(f"[OpenStack] Subnet creada: {_created_subnet.name} (CIDR: {cidr}, GW: {gateway_ip}, DNS: {_dns or 'NINGUNO'})")
                if not _dns:
                    logger.warning("[OpenStack] ⚠️  Subnet sin dns_nameservers y con DHCP deshabilitado — "
                                   "las VMs saldrán a Internet por IP pero NO resolverán nombres.")

                # 3. Crear Security Group dedicado
                _created_sec_group = await asyncio.to_thread(
                    conn.network.create_security_group,
                    name=secgroup_name,
                    description=f"Security group for slice {slice_id}"
                )
                logger.info(f"[OpenStack] Security Group creado: {_created_sec_group.name} (ID: {_created_sec_group.id})")

                await asyncio.to_thread(
                    conn.network.create_security_group_rule,
                    security_group_id=_created_sec_group.id,
                    direction="ingress", protocol="icmp", ethertype="IPv4"
                )
                await asyncio.to_thread(
                    conn.network.create_security_group_rule,
                    security_group_id=_created_sec_group.id,
                    direction="ingress", protocol="tcp",
                    port_range_min=22, port_range_max=22, ethertype="IPv4"
                )

                # Security Group para VMs sin acceso a internet (bloqueo egress)
                secgroup_no_internet_name = f"secgroup-slice-{slice_id}-no-internet"
                _created_sec_group_no_internet = await asyncio.to_thread(
                    conn.network.create_security_group,
                    name=secgroup_no_internet_name,
                    description=f"Security group for slice {slice_id} (No Internet)"
                )
                logger.info(f"[OpenStack] Security Group No-Internet creado: {_created_sec_group_no_internet.name} (ID: {_created_sec_group_no_internet.id})")

                await asyncio.to_thread(
                    conn.network.create_security_group_rule,
                    security_group_id=_created_sec_group_no_internet.id,
                    direction="ingress", protocol="icmp", ethertype="IPv4"
                )
                await asyncio.to_thread(
                    conn.network.create_security_group_rule,
                    security_group_id=_created_sec_group_no_internet.id,
                    direction="ingress", protocol="tcp",
                    port_range_min=22, port_range_max=22, ethertype="IPv4"
                )

                # Eliminar regla default egress IPv4 para restringir internet
                try:
                    rules = list(await asyncio.to_thread(conn.network.security_group_rules, security_group_id=_created_sec_group_no_internet.id))
                    for r in rules:
                        if r.direction == "egress" and r.ether_type == "IPv4":
                            logger.info(f"[OpenStack] Eliminando regla default egress IPv4 de {secgroup_no_internet_name}: {r.id}")
                            await asyncio.to_thread(conn.network.delete_security_group_rule, r.id)
                except Exception as e:
                    logger.error(f"[OpenStack] Falló al eliminar regla default egress IPv4: {e}")

                # Permitir salida únicamente a la red de gestión del slice
                await asyncio.to_thread(
                    conn.network.create_security_group_rule,
                    security_group_id=_created_sec_group_no_internet.id,
                    direction="egress",
                    ethertype="IPv4",
                    remote_ip_prefix=cidr
                )

                # 4. Configurar Enrutamiento L3 (Router + Gateway Externo + Subnet Interface)
                ext_net = await asyncio.to_thread(conn.network.find_network, settings.OS_EXTERNAL_NETWORK_NAME)
                ext_subnet = None
                if ext_net:
                    ext_subnet = await asyncio.to_thread(conn.network.find_subnet, settings.OS_EXTERNAL_SUBNET_NAME)
                    if ext_subnet:
                        # El next-hop del SNAT del slice. Si esta IP no responde
                        # ARP en el segmento L2 de la red externa, el router
                        # devuelve ICMP Host Unreachable a las VMs y NO hay
                        # salida a Internet, por más que el router exista y
                        # enable_snat sea true. Se loguea para poder contrastarlo
                        # contra el gateway real del segmento.
                        logger.info(f"[OpenStack] external_subnet existente: {ext_subnet.cidr} "
                                    f"gateway_ip={ext_subnet.gateway_ip} (next-hop del SNAT de TODOS los slices)")
                    if not ext_subnet:
                        # OJO: se está inventando infraestructura COMPARTIDA a
                        # partir de defaults del código. Si OS_EXTERNAL_GATEWAY_IP
                        # no es el gateway real del segmento físico, la subnet
                        # queda creada con un next-hop muerto — y persiste, así
                        # que todos los deploys posteriores heredan el fallo.
                        logger.warning(
                            f"[OpenStack] ⚠️  '{settings.OS_EXTERNAL_SUBNET_NAME}' no existe — se creará con valores "
                            f"del código (cidr={settings.OS_EXTERNAL_SUBNET_CIDR}, gw={settings.OS_EXTERNAL_GATEWAY_IP}). "
                            f"VERIFICAR que ese gateway sea el real del segmento, o no habrá salida a Internet.")
                        # Banda reservada a Neutron (puertos qg-); el resto del /24
                        # es territorio de la tabla `ip_pool` del SliceManager.
                        _ext_subnet_args = dict(
                            name=settings.OS_EXTERNAL_SUBNET_NAME,
                            network_id=ext_net.id,
                            ip_version=4,
                            cidr=settings.OS_EXTERNAL_SUBNET_CIDR,
                            gateway_ip=settings.OS_EXTERNAL_GATEWAY_IP,
                            enable_dhcp=True,
                        )
                        _pool = (settings.OS_EXTERNAL_ALLOCATION_POOL or "").strip()
                        if "-" in _pool:
                            _start, _end = (p.strip() for p in _pool.split("-", 1))
                            _ext_subnet_args["allocation_pools"] = [{"start": _start, "end": _end}]
                        try:
                            ext_subnet = await asyncio.to_thread(
                                conn.network.create_subnet, **_ext_subnet_args
                            )
                            logger.info(f"[OpenStack] external_subnet creada: {settings.OS_EXTERNAL_SUBNET_CIDR} "
                                        f"(allocation_pool Neutron: {_pool or 'completo'})")
                        except Exception as e:
                            logger.error(f"[OpenStack] No se pudo crear external_subnet: {e}")
                            ext_subnet = None

                    # Crear Router virtual conectando la red interna con la externa
                    try:
                        _created_router = await asyncio.to_thread(
                            conn.network.create_router,
                            name=router_name,
                            external_gateway_info={"network_id": ext_net.id}
                        )
                        # IP del puerto qg- del router: es la dirección con la que
                        # salen NATeadas las VMs del slice que NO tienen floating
                        # IP. Cuando una VM reporta "Destination Host Unreachable"
                        # desde ESTA IP, el que no puede resolver el next-hop es
                        # el router, no la VM.
                        _gw_info = getattr(_created_router, "external_gateway_info", None) or {}
                        _gw_ips = [f.get("ip_address") for f in (_gw_info.get("external_fixed_ips") or [])]
                        logger.info(f"[OpenStack] Router virtual creado: {_created_router.name} (ID: {_created_router.id}) "
                                    f"— IP de SNAT del slice: {_gw_ips or 'SIN IP EXTERNA'}")

                        # Conectar subred interna del slice al router (Gateway del slice)
                        await asyncio.to_thread(
                            conn.network.add_interface_to_router,
                            _created_router.id,
                            subnet_id=_created_subnet.id
                        )
                        logger.info(f"[OpenStack] Subred {_created_subnet.name} conectada al router {_created_router.name}")
                    except Exception as e:
                        logger.error(f"[OpenStack] Fallo al configurar Router/Interfaz en OpenStack: {e}")
                        raise e
                else:
                    # Antes esto era un warning y el deploy seguía: se creaban las
                    # VMs sobre una red SIN router, con un gateway_ip que nadie
                    # contesta, y el slice se reportaba SUCCESS. Sin salida a
                    # Internet y sin acceso externo, sin ninguna señal de error.
                    # Es un error de configuración, no una degradación tolerable.
                    raise RuntimeError(
                        f"Red externa '{settings.OS_EXTERNAL_NETWORK_NAME}' no encontrada en Neutron. "
                        f"Sin ella no hay router, ni salida a Internet, ni Floating IPs. "
                        f"Revisar OS_EXTERNAL_NETWORK_NAME (`openstack network list --external`)."
                    )
            else:
                # EXTEND: la red externa solo se necesita para Floating IPs de VMs nuevas
                ext_net = await asyncio.to_thread(conn.network.find_network, settings.OS_EXTERNAL_NETWORK_NAME)
                ext_subnet = None

            # 5. Crear puerto Neutron para cada VM (red interna del slice + opcional Floating IP)
            port_map = {}
            # vm_id → puerto de gestión (el que lleva la Floating IP) — se usa
            # para el SG de ingreso desde Internet (6.c), separado del SG de
            # Firewall Interno que solo va en los puertos de enlace.
            vm_mgmt_port_map: dict = {}
            # vm_id → lista de puertos Neutron de ENLACE (no gestión) para asignar
            # el SG de Firewall Interno per-VM más abajo (6.a)
            vm_link_ports_map: dict = {}
            for vm in request.vms:
                # Modo extend: las VMs ya desplegadas no reciben puerto de gestión
                # nuevo; solo entradas para registrar sus puertos de enlace.
                if getattr(vm, "already_deployed", False):
                    port_map[vm.vm_id] = {
                        "provider_port_id": None,
                        "external_port_id": None,
                        "external_ip": None,
                        "link_ports": [],
                    }
                    continue
                # Si la VM no tiene acceso a internet, le asignamos el Security Group restrictivo
                sg_id = _created_sec_group.id
                if getattr(vm, "internet_access", 1) == 0:
                    sg_id = _created_sec_group_no_internet.id
                    logger.info(f"[OpenStack] VM {vm.vm_id} sin salida a internet → aplicando SG restrictivo: {_created_sec_group_no_internet.name}")
                else:
                    logger.info(f"[OpenStack] VM {vm.vm_id} con salida a internet → aplicando SG default: {_created_sec_group.name}")

                port = await asyncio.to_thread(
                    conn.network.create_port,
                    name=f"port-{slice_id}-{vm.vm_id}",
                    network_id=_created_network.id,
                    fixed_ips=[{"ip_address": vm.internal_ip}],
                    security_groups=[sg_id]
                )
                _created_ports.append(port)
                vm_mgmt_port_map[vm.vm_id] = port
                logger.info(f"[OpenStack] Puerto Neutron creado para VM {vm.vm_id} con IP fija: {vm.internal_ip}")

                external_ip = None
                # Si la VM requiere IP externa (acceso SSH), asignamos una Floating IP
                if vm.external_ip and ext_net:
                    try:
                        # Creamos y asociamos la Floating IP al puerto interno de la VM
                        fip_kwargs = {
                            "floating_network_id": ext_net.id,
                            "port_id": port.id
                        }
                        if vm.external_ip and vm.external_ip != "random":
                            fip_kwargs["floating_ip_address"] = vm.external_ip

                        fip = await asyncio.to_thread(conn.network.create_ip, **fip_kwargs)
                        external_ip = fip.floating_ip_address
                        logger.info(f"[OpenStack] Floating IP asociada a VM {vm.vm_id}: {external_ip}")
                    except Exception as e:
                        # La IP explícita viene de la tabla `ip_pool` del SliceManager,
                        # que reparte 10.60.16.0/24 SIN coordinarse con el
                        # allocation_pool de la subnet externa de Neutron — de ahí
                        # salen también el puerto qg- del router de CADA slice y
                        # cualquier otra FIP. Cuando chocan, Neutron devuelve 409 y
                        # antes la VM se quedaba sin IP externa con el deploy en
                        # SUCCESS. Ahora se reintenta dejando elegir a Neutron: la
                        # IP resultante viaja en el port_map y el SliceManager la
                        # persiste (nats_listener), así que la UI muestra la real.
                        if "floating_ip_address" in fip_kwargs:
                            logger.warning(
                                f"[OpenStack] IP externa {vm.external_ip} no disponible para VM {vm.vm_id} "
                                f"({e}) — reintentando con asignación automática de Neutron.")
                            try:
                                fip_kwargs.pop("floating_ip_address")
                                fip = await asyncio.to_thread(conn.network.create_ip, **fip_kwargs)
                                external_ip = fip.floating_ip_address
                                logger.info(f"[OpenStack] Floating IP (auto) asociada a VM {vm.vm_id}: {external_ip} "
                                            f"— sustituye a {vm.external_ip}")
                            except Exception as e2:
                                logger.error(f"[OpenStack] ❌ ACCESO EXTERNO NO DISPONIBLE para VM {vm.vm_id}: {e2}")
                        else:
                            logger.error(f"[OpenStack] ❌ ACCESO EXTERNO NO DISPONIBLE para VM {vm.vm_id}: {e}")

                port_map[vm.vm_id] = {
                    "provider_port_id": port.id,
                    "external_port_id": None,  # Ya no se requiere un segundo puerto físico virtual adjunto a la VM
                    "external_ip": external_ip,
                    "link_ports": [],  # Inicializar lista de puertos de enlaces
                }

            # 6. Crear Redes, Subredes y Puertos para cada enlace (link) del slice
            # Q-in-Q: acumular, por compute (host_map), los C-VIDs reales que
            # Neutron asigna a cada net-link, para tunelizarlos bajo el S-VID.
            host_map = request.host_map or {}
            qinq_s_vlan = 0
            qinq_host_cvlans: dict = {}
            for link in request.links:
                vlan_id = link.vlan_id
                vm1_id = link.vm1_id
                vm2_id = link.vm2_id
                logger.info(f"[OpenStack] Configurando enlace {link.connection_id} (VLAN {vlan_id}) entre {vm1_id} y {vm2_id}")

                try:
                    # Crear red para el enlace. Se FUERZA el segmentation_id al
                    # C-VID que reservó el SliceManager (opción 1 → `vlans` = tag
                    # real). Requiere physnet configurado; si no, cae a tenant.
                    # Misma MTU que la red de gestión: los enlaces también van
                    # etiquetados sobre el trunk, así que sin esto una
                    # transferencia grande VM↔VM se cuelga igual que el SSH.
                    link_net_args = {"name": f"net-link-{vlan_id}"}
                    if settings.OS_NETWORK_MTU:
                        link_net_args["mtu"] = settings.OS_NETWORK_MTU
                    _lprov_phys = os.getenv("OS_PROVIDER_PHYSICAL_NETWORK")
                    if _lprov_phys:
                        link_net_args["provider_network_type"] = "vlan"
                        link_net_args["provider_physical_network"] = _lprov_phys
                        link_net_args["provider_segmentation_id"] = int(vlan_id)
                    net_link = await asyncio.to_thread(
                        conn.network.create_network, **link_net_args
                    )
                    _created_link_networks.append(net_link.id)

                    # Subnet del enlace (sin DHCP, punto a punto). El CIDR y los
                    # fixed_ips salen de las IPs manuales del usuario si las hay;
                    # si no, es el /30 con .1/.2 de siempre (ver _resolve_link_subnet).
                    link_cidr, fixed1, fixed2 = _resolve_link_subnet(
                        vlan_id,
                        getattr(link, "vm1_link_ip", None),
                        getattr(link, "vm2_link_ip", None),
                    )
                    sub_link = await asyncio.to_thread(
                        conn.network.create_subnet,
                        name=f"subnet-link-{vlan_id}",
                        network_id=net_link.id,
                        ip_version=4,
                        cidr=link_cidr,
                        enable_dhcp=False,
                        gateway_ip=None
                    )
                    _created_link_subnets.append(sub_link.id)

                    # Puerto VM1 - Port Security desactivado para permitir routing/IPs libres.
                    # Nova exige un FixedIP en todo puerto adjuntado al boot ("Port ... requires a
                    # FixedIP in order to be used") — fixed_ips=[] hace fallar create_server con 400.
                    # Si el usuario definió IP → esa IP; si no → una libre de la subnet (auto).
                    fixed_ips_1 = [{"ip_address": fixed1}] if fixed1 else [{"subnet_id": sub_link.id}]
                    port1 = await asyncio.to_thread(
                        conn.network.create_port,
                        name=f"port-link-{vlan_id}-{vm1_id}",
                        network_id=net_link.id,
                        port_security_enabled=False,
                        fixed_ips=fixed_ips_1
                    )
                    _created_ports.append(port1)

                    # Puerto VM2 - Port Security desactivado para permitir routing/IPs libres
                    fixed_ips_2 = [{"ip_address": fixed2}] if fixed2 else [{"subnet_id": sub_link.id}]
                    port2 = await asyncio.to_thread(
                        conn.network.create_port,
                        name=f"port-link-{vlan_id}-{vm2_id}",
                        network_id=net_link.id,
                        port_security_enabled=False,
                        fixed_ips=fixed_ips_2
                    )
                    _created_ports.append(port2)

                    # Registrar puertos en el port_map de cada VM
                    if vm1_id in port_map:
                        port_map[vm1_id]["link_ports"].append(port1.id)
                    if vm2_id in port_map:
                        port_map[vm2_id]["link_ports"].append(port2.id)
                    # Registrar objetos de puerto por-VM para posterior aplicación de SG per-VM
                    vm_link_ports_map.setdefault(vm1_id, []).append(port1)
                    vm_link_ports_map.setdefault(vm2_id, []).append(port2)

                    # Q-in-Q: el C-VID real es el segmentation_id que Neutron
                    # asignó a la red del enlace (no el vlan_id lógico). Se
                    # relee la red para obtener el atributo provider.
                    if getattr(link, "s_vlan_id", 0):
                        qinq_s_vlan = link.s_vlan_id
                        net_full = await asyncio.to_thread(conn.network.get_network, net_link.id)
                        seg = getattr(net_full, "provider_segmentation_id", None)
                        if seg:
                            for vid in (vm1_id, vm2_id):
                                host = host_map.get(vid)
                                if host:
                                    qinq_host_cvlans.setdefault(host, set()).add(int(seg))

                    logger.info(f"[OpenStack] Enlace creado exitosamente: {net_link.name}")
                except Exception as e:
                    logger.error(f"[OpenStack] Error creando enlace {link.connection_id}: {e}")
                    raise e

            # 6.a Security Groups por VM (R5 / prueba 5.4.1 NETWORK_SECURITY_RULES)
            # ─────────────────────────────────────────────────────────────────
            # Se agregan las reglas del usuario (vm1_security_rules /
            # vm2_security_rules de cada NetworkLink) por vm_id. Si una VM
            # aparece con reglas, se crea un SG dedicado con SOLO esas reglas
            # (más ICMP para diagnóstico), se activa port_security en SUS
            # PUERTOS DE ENLACE y se les asigna ese SG. El puerto de gestión
            # (Floating IP) queda fuera de este SG a propósito — lo gobierna
            # el SG de ingreso de 6.b (Reglas de Entrada desde Internet), que
            # responde a una pregunta distinta (quién entra desde la IP
            # externa, no quién le habla a la VM dentro del slice).
            # Sin esto, la prueba falla porque:
            #  · el SG por defecto del slice hardcodea ICMP+SSH, ignorando
            #    lo que el usuario configuró en el WebApp;
            #  · los puertos de enlace se crean con port_security=False,
            #    permitiendo que `nc -vz <ip> 80` pase entre VMs del slice.
            vm_rules_map: dict = {}  # vm_id → list[SecurityRule]
            for link in request.links:
                if link.vm1_security_rules:
                    vm_rules_map.setdefault(link.vm1_id, []).extend(link.vm1_security_rules)
                if link.vm2_security_rules:
                    vm_rules_map.setdefault(link.vm2_id, []).extend(link.vm2_security_rules)

            for vm_id, rules in vm_rules_map.items():
                # De-duplicar (protocol, allow_port)
                seen = set()
                unique_rules = []
                for r in rules:
                    key = (r.protocol.lower(), int(r.allow_port))
                    if key not in seen:
                        seen.add(key)
                        unique_rules.append(r)

                sg_name = f"secgroup-slice-{slice_id}-vm-{vm_id}"
                try:
                    vm_sg = await asyncio.to_thread(
                        conn.network.create_security_group,
                        name=sg_name,
                        description=f"User-defined rules for slice {slice_id} VM {vm_id}"
                    )
                    _created_sec_group_per_vm.append(vm_sg)
                    logger.info(f"[OpenStack] SG por VM creado: {sg_name} ({vm_sg.id})")

                    # ICMP siempre permitido (diagnóstico / ping entre VMs del slice)
                    await asyncio.to_thread(
                        conn.network.create_security_group_rule,
                        security_group_id=vm_sg.id,
                        direction="ingress", protocol="icmp", ethertype="IPv4"
                    )
                    # Reglas del usuario
                    for r in unique_rules:
                        await asyncio.to_thread(
                            conn.network.create_security_group_rule,
                            security_group_id=vm_sg.id,
                            direction="ingress",
                            protocol=r.protocol.lower(),
                            port_range_min=int(r.allow_port),
                            port_range_max=int(r.allow_port),
                            ethertype="IPv4",
                        )
                        logger.info(f"[OpenStack]  ↳ ingress {r.protocol}/{r.allow_port} (VM {vm_id})")

                    # Aplicar el SG SOLO a los puertos de ENLACE de la VM (Firewall
                    # Interno = tráfico VM↔VM dentro del slice). El puerto de
                    # gestión (Floating IP) NO se toca acá — lo gobierna el SG de
                    # ingreso de 6.c, o el SG default/no-internet si la VM no tiene
                    # IP externa. Requiere port_security_enabled=True en cada
                    # puerto (los de enlace se crearon con False; se actualiza aquí).
                    for p in vm_link_ports_map.get(vm_id, []):
                        try:
                            await asyncio.to_thread(
                                conn.network.update_port,
                                p.id,
                                port_security_enabled=True,
                                security_group_ids=[vm_sg.id],
                            )
                            logger.info(f"[OpenStack]  ↳ puerto {p.name} ({p.id}) → SG {sg_name}")
                        except Exception as e:
                            logger.error(f"[OpenStack] No se pudo asignar SG {sg_name} al puerto {p.id}: {e}")
                            raise
                except Exception as e:
                    logger.error(f"[OpenStack] Error creando/aplicando SG por VM {vm_id}: {e}")
                    raise

            # 6.b Security Group de Ingreso desde Internet (AWS-style, deny-by-default)
            # ─────────────────────────────────────────────────────────────────
            # A diferencia de 6.a (Firewall Interno, solo puertos de enlace), este
            # SG gobierna qué entra por la Floating IP del puerto de gestión.
            # Se aplica SOLO a VMs con external_ip; reemplaza el SG default/
            # no-internet que ese puerto traía desde su creación (sin ICMP/SSH
            # implícitos — deny-by-default puro, el usuario declara cada regla).
            for vm in request.vms:
                if getattr(vm, "already_deployed", False) or not getattr(vm, "external_ip", None):
                    continue
                mgmt_port = vm_mgmt_port_map.get(vm.vm_id)
                if not mgmt_port:
                    continue

                seen = set()
                unique_ingress = []
                for r in (getattr(vm, "ingress_rules", None) or []):
                    key = (r.protocol.lower(), int(r.allow_port))
                    if key not in seen:
                        seen.add(key)
                        unique_ingress.append(r)

                sg_name = f"secgroup-slice-{slice_id}-vm-{vm.vm_id}-ingress"
                try:
                    ingress_sg = await asyncio.to_thread(
                        conn.network.create_security_group,
                        name=sg_name,
                        description=f"Internet ingress rules for slice {slice_id} VM {vm.vm_id}"
                    )
                    _created_sec_group_per_vm.append(ingress_sg)
                    logger.info(f"[OpenStack] SG de ingreso creado: {sg_name} ({ingress_sg.id})")

                    for r in unique_ingress:
                        rule_kwargs = dict(
                            security_group_id=ingress_sg.id,
                            direction="ingress", ethertype="IPv4",
                        )
                        if r.protocol.lower() == "icmp":
                            rule_kwargs["protocol"] = "icmp"
                        else:
                            rule_kwargs.update(
                                protocol=r.protocol.lower(),
                                port_range_min=int(r.allow_port),
                                port_range_max=int(r.allow_port),
                            )
                        await asyncio.to_thread(conn.network.create_security_group_rule, **rule_kwargs)
                        logger.info(f"[OpenStack]  ↳ ingress {r.protocol}/{r.allow_port} (VM {vm.vm_id}, desde Internet)")

                    if not unique_ingress:
                        # Deny-by-default puro: este SG reemplaza al default del
                        # slice (que traía ICMP+SSH) y se queda con CERO reglas de
                        # ingreso. La Floating IP queda inalcanzable — ni ping ni
                        # SSH — aunque esté perfectamente creada y asociada. Es el
                        # comportamiento buscado, pero desde fuera es idéntico a
                        # "el acceso externo no funciona": se deja explícito.
                        logger.warning(
                            f"[OpenStack] ⚠️  VM {vm.vm_id} tiene IP externa pero NINGUNA regla de entrada "
                            f"declarada — la Floating IP quedará inalcanzable (sin SSH ni ICMP). "
                            f"Agregar reglas de entrada en el NodeEditor.")

                    await asyncio.to_thread(
                        conn.network.update_port,
                        mgmt_port.id,
                        port_security_enabled=True,
                        security_group_ids=[ingress_sg.id],
                    )
                    logger.info(f"[OpenStack]  ↳ puerto de gestión {mgmt_port.name} ({mgmt_port.id}) → SG {sg_name} "
                                f"({len(unique_ingress)} regla(s))")
                except Exception as e:
                    logger.error(f"[OpenStack] Error creando/aplicando SG de ingreso para VM {vm.vm_id}: {e}")
                    raise

            # 6.c Q-in-Q (802.1ad): interponer el dot1q-tunnel en cada compute
            # del slice ANTES de que Compute cree las VMs (pre-compute, como Linux).
            if qinq_s_vlan and qinq_host_cvlans:
                logger.info(f"[OpenStack][QinQ] Aplicando S-VID {qinq_s_vlan} en {len(qinq_host_cvlans)} compute(s)")
                await _qinq_apply(qinq_s_vlan, qinq_host_cvlans, request.compute_ssh_map or {})

            links_ok = [LinkResult(connection_id=link.connection_id) for link in request.links]
            return DeployNetworkResponse(
                slice_id=slice_id,
                request_id=request.request_id,
                status=ProvisioningStatus.SUCCESS,
                links_ok=links_ok,
                links_failed=[],
                port_map=port_map
            )

        except Exception as exc:
            logger.error(f"[OpenStack] Error crítico durante deploy de red: {exc}", exc_info=True)
            try:
                await _rollback(conn)
            except Exception as rb_exc:
                logger.error(f"[OpenStack] Error durante rollback: {rb_exc}")
            links_failed = [LinkResult(connection_id=link.connection_id, error=str(exc)) for link in request.links]
            return DeployNetworkResponse(
                slice_id=slice_id,
                request_id=request.request_id,
                status=ProvisioningStatus.ERROR,
                links_ok=[],
                links_failed=links_failed,
                port_map={}
            )

    async def destroy(self, request: DestroyNetworkRequest) -> DestroyNetworkResponse:
        slice_id = request.slice_id
        is_shrink = getattr(request, "mode", "full") == "shrink"
        logger.info(f"[OpenStack] Iniciando {'SHRINK' if is_shrink else 'destrucción'} de red para slice {slice_id}")

        # ── SHRINK: solo limpiar las redes de enlace eliminadas y los puertos
        # de las VMs borradas. NO tocar net-slice / router / SGs (siguen vivos). ──
        if is_shrink:
            try:
                conn = await asyncio.to_thread(get_connection)
                # 1. Redes de enlace eliminadas (net-link-{vlan}) + sus puertos/subnets
                for link in (request.links or []):
                    vlan_id = link.vlan_id
                    net = await asyncio.to_thread(conn.network.find_network, f"net-link-{vlan_id}")
                    if not net:
                        continue
                    for port in list(await asyncio.to_thread(conn.network.ports, network_id=net.id)):
                        try:
                            await asyncio.to_thread(conn.network.delete_port, port.id)
                        except Exception as e:
                            logger.warning(f"[OpenStack][SHRINK] puerto de enlace {port.id}: {e}")
                    sub = await asyncio.to_thread(conn.network.find_subnet, f"subnet-link-{vlan_id}")
                    if sub:
                        try:
                            await asyncio.to_thread(conn.network.delete_subnet, sub.id)
                        except Exception as e:
                            logger.warning(f"[OpenStack][SHRINK] subnet de enlace {sub.id}: {e}")
                    try:
                        await asyncio.to_thread(conn.network.delete_network, net.id)
                        logger.info(f"[OpenStack][SHRINK] red de enlace net-link-{vlan_id} eliminada")
                    except Exception as e:
                        logger.warning(f"[OpenStack][SHRINK] red de enlace {net.id}: {e}")
                # 2. Puertos de gestión + floating IPs de las VMs eliminadas
                for vm in (request.vms or []):
                    pname = f"port-{slice_id}-{vm.vm_id}"
                    port = await asyncio.to_thread(conn.network.find_port, pname)
                    if not port:
                        continue
                    for fip in list(await asyncio.to_thread(conn.network.ips, port_id=port.id)):
                        try:
                            await asyncio.to_thread(conn.network.delete_ip, fip.id)
                        except Exception:
                            pass
                    # el puerto lo libera Nova al borrar la instancia; intento best-effort
                    try:
                        await asyncio.to_thread(conn.network.delete_port, port.id)
                    except Exception as e:
                        logger.warning(f"[OpenStack][SHRINK] puerto de gestión {pname}: {e}")
                return DestroyNetworkResponse(slice_id=slice_id, request_id=request.request_id,
                                              status=ProvisioningStatus.SUCCESS)
            except Exception as exc:
                logger.error(f"[OpenStack][SHRINK] Error: {exc}", exc_info=True)
                return DestroyNetworkResponse(slice_id=slice_id, request_id=request.request_id,
                                              status=ProvisioningStatus.ERROR, error=str(exc))

        try:
            conn = await asyncio.to_thread(get_connection)

            network_name = f"net-slice-{slice_id}"
            subnet_name = f"subnet-slice-{slice_id}"
            secgroup_name = f"secgroup-slice-{slice_id}"
            router_name = f"router-slice-{slice_id}"

            # 0.a Q-in-Q: quitar el dot1q-tunnel del slice en los computes
            # (S-VID leído de los links; independiente de la limpieza Neutron).
            _s_vlan = next((l.s_vlan_id for l in (request.links or []) if getattr(l, "s_vlan_id", 0)), 0)
            if _s_vlan and getattr(request, "compute_ssh_map", None):
                await _qinq_teardown(_s_vlan, request.compute_ssh_map)

            # 0.b Purga idempotente a prueba de duplicados: borra por ID TODOS los
            # routers/redes/SGs homónimos del slice (resuelve el caso "More than
            # one X exists with the name..."). Los bloques siguientes quedan como
            # respaldo (no-ops si esto ya limpió todo) + puertos externos.
            try:
                await _purge_slice_networking(
                    conn, slice_id, [l.vlan_id for l in (request.links or [])])
            except Exception as _pexc:
                logger.warning(f"[OpenStack] Destroy: purga best-effort falló: {_pexc}")

            # 0. Borrar redes, subredes y puertos de enlaces (links) punto a punto
            if request.links:
                for link in request.links:
                    vlan_id = link.vlan_id
                    net_name = f"net-link-{vlan_id}"
                    sub_name = f"subnet-link-{vlan_id}"
                    logger.info(f"[OpenStack] Limpiando recursos de enlace VLAN {vlan_id} ({net_name})")
                    
                    net = await asyncio.to_thread(conn.network.find_network, net_name)
                    if net:
                        # Buscar y borrar puertos en esta red de enlace
                        try:
                            ports = list(await asyncio.to_thread(conn.network.ports, network_id=net.id))
                            for port in ports:
                                try:
                                    logger.info(f"[OpenStack] Eliminando puerto de enlace: {port.name or port.id}")
                                    await asyncio.to_thread(conn.network.delete_port, port.id)
                                except Exception as e:
                                    logger.warning(f"[OpenStack] Error borrando puerto de enlace {port.id}: {e}")
                        except Exception as e:
                            logger.warning(f"[OpenStack] Error listando puertos para red de enlace {net_name}: {e}")
                        
                        # Borrar subnet
                        sub = await asyncio.to_thread(conn.network.find_subnet, sub_name)
                        if sub:
                            try:
                                logger.info(f"[OpenStack] Eliminando subnet de enlace: {sub.name}")
                                await asyncio.to_thread(conn.network.delete_subnet, sub.id)
                            except Exception as e:
                                logger.warning(f"[OpenStack] Error borrando subnet de enlace {sub.id}: {e}")
                        
                        # Borrar red
                        try:
                            logger.info(f"[OpenStack] Eliminando red de enlace: {net.name}")
                            await asyncio.to_thread(conn.network.delete_network, net.id)
                        except Exception as e:
                            logger.warning(f"[OpenStack] Error borrando red de enlace {net.id}: {e}")

            network = await asyncio.to_thread(conn.network.find_network, network_name)
            subnet = await asyncio.to_thread(conn.network.find_subnet, subnet_name)
            router = await asyncio.to_thread(conn.network.find_router, router_name)
            
            # 1. Borrar IPs Flotantes y Puertos
            if network:
                try:
                    ports = list(await asyncio.to_thread(conn.network.ports, network_id=network.id))
                    for port in ports:
                        try:
                            fips = list(await asyncio.to_thread(conn.network.ips, port_id=port.id))
                            for fip in fips:
                                try:
                                    logger.info(f"[OpenStack] Liberando IP flotante: {fip.floating_ip_address}")
                                    await asyncio.to_thread(conn.network.delete_ip, fip.id)
                                except Exception as e:
                                    logger.warning(f"[OpenStack] Error al liberar IP flotante {fip.id}: {e}")
                            logger.info(f"[OpenStack] Eliminando puerto Neutron: {port.id}")
                            await asyncio.to_thread(conn.network.delete_port, port.id)
                        except Exception as e:
                            logger.warning(f"[OpenStack] Error al eliminar puerto Neutron {port.id}: {e}")
                except Exception as e:
                    logger.warning(f"[OpenStack] Error al listar o procesar puertos de red {network_name}: {e}")
            
            # 1.5 Borrar puertos externos (red provider "external", compartida — no se borra la red)
            try:
                ext_net = await asyncio.to_thread(conn.network.find_network, settings.OS_EXTERNAL_NETWORK_NAME)
                if ext_net:
                    ext_ports = list(await asyncio.to_thread(conn.network.ports, network_id=ext_net.id))
                    prefix = f"port-ext-{slice_id}-"
                    for port in ext_ports:
                        if (port.name or "").startswith(prefix):
                            try:
                                logger.info(f"[OpenStack] Eliminando puerto externo: {port.name} ({port.id})")
                                await asyncio.to_thread(conn.network.delete_port, port.id)
                            except Exception as e:
                                logger.warning(f"[OpenStack] No se pudo borrar puerto externo {port.id} (puede que Nova aún lo tenga adjunto): {e}")
            except Exception as e:
                logger.warning(f"[OpenStack] Error limpiando puertos externos: {e}")

            # 2. Desconectar subnet y borrar router
            if router:
                if subnet:
                    try:
                        logger.info(f"[OpenStack] Desconectando subnet del router virtual")
                        await asyncio.to_thread(
                            conn.network.remove_interface_from_router,
                            router.id,
                            subnet_id=subnet.id
                        )
                    except Exception as e:
                        logger.warning(f"[OpenStack] No se pudo desasociar la subnet del router: {e}")
                try:
                    logger.info(f"[OpenStack] Eliminando router virtual: {router.name}")
                    await asyncio.to_thread(conn.network.delete_router, router.id)
                except Exception as e:
                    logger.warning(f"[OpenStack] No se pudo eliminar router virtual: {e}")
                
            # 3. Eliminar Subred
            if subnet:
                try:
                    logger.info(f"[OpenStack] Eliminando subnet: {subnet.name}")
                    await asyncio.to_thread(conn.network.delete_subnet, subnet.id)
                except Exception as e:
                    logger.warning(f"[OpenStack] No se pudo eliminar subnet: {e}")
                
            # 4. Eliminar Red
            if network:
                try:
                    logger.info(f"[OpenStack] Eliminando red: {network.name}")
                    await asyncio.to_thread(conn.network.delete_network, network.id)
                except Exception as e:
                    logger.warning(f"[OpenStack] No se pudo eliminar red: {e}")
                
            # 5. Eliminar Security Groups
            try:
                sec_group = await asyncio.to_thread(conn.network.find_security_group, secgroup_name)
                if sec_group:
                    logger.info(f"[OpenStack] Eliminando Security Group: {sec_group.name}")
                    await asyncio.to_thread(conn.network.delete_security_group, sec_group.id)
            except Exception as e:
                logger.warning(f"[OpenStack] No se pudo eliminar Security Group {secgroup_name}: {e}")
                
            try:
                sec_group_no_int = await asyncio.to_thread(conn.network.find_security_group, f"secgroup-slice-{slice_id}-no-internet")
                if sec_group_no_int:
                    logger.info(f"[OpenStack] Eliminando Security Group No-Internet: {sec_group_no_int.name}")
                    await asyncio.to_thread(conn.network.delete_security_group, sec_group_no_int.id)
            except Exception as e:
                logger.warning(f"[OpenStack] No se pudo eliminar Security Group No-Internet: {e}")

            # Security Groups por VM (secgroup-slice-{slice_id}-vm-*)
            try:
                sg_vm_prefix = f"secgroup-slice-{slice_id}-vm-"
                all_sgs = list(await asyncio.to_thread(conn.network.security_groups))
                for sg in all_sgs:
                    if getattr(sg, "name", "").startswith(sg_vm_prefix):
                        try:
                            await asyncio.to_thread(conn.network.delete_security_group, sg.id)
                            logger.info(f"[OpenStack] SG por VM eliminado: {sg.name}")
                        except Exception as e:
                            logger.warning(f"[OpenStack] No se pudo eliminar SG por VM {sg.name}: {e}")
            except Exception as e:
                logger.warning(f"[OpenStack] Error listando SGs por VM: {e}")
                
            logger.info(f"[OpenStack] Destrucción de red completada para slice {slice_id}")
            return DestroyNetworkResponse(
                slice_id=slice_id,
                request_id=request.request_id,
                status=ProvisioningStatus.SUCCESS
            )
            
        except Exception as exc:
            logger.error(f"[OpenStack] Error crítico durante destroy de red: {exc}", exc_info=True)
            return DestroyNetworkResponse(
                slice_id=slice_id,
                request_id=request.request_id,
                status=ProvisioningStatus.ERROR,
                error=str(exc)
            )
