import os
import logging
import asyncio
import openstack
from app.core.config import settings
from app.models.schemas import (
    DeployNetworkRequest, DeployNetworkResponse, DestroyNetworkRequest, DestroyNetworkResponse,
    LinkResult, ProvisioningStatus
)

logger = logging.getLogger("network-orchestrator.openstack")

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

        async def _rollback(conn):
            """Best-effort cleanup of resources created so far."""
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

        try:
            conn = await asyncio.to_thread(get_connection)

            # 1. Crear Provider Network
            network_args = {"name": network_name}
            if os.getenv("OS_VLAN_TRANSPARENT", "").lower() in ("1", "true", "yes"):
                network_args["vlan_transparent"] = True
            prov_type = os.getenv("OS_PROVIDER_NETWORK_TYPE")
            if prov_type:
                network_args["provider_network_type"] = prov_type
            prov_phys = os.getenv("OS_PROVIDER_PHYSICAL_NETWORK")
            if prov_phys:
                network_args["provider_physical_network"] = prov_phys
            prov_seg = os.getenv("OS_PROVIDER_SEGMENTATION_ID")
            if prov_seg:
                network_args["provider_segmentation_id"] = int(prov_seg)

            _created_network = await asyncio.to_thread(conn.network.create_network, **network_args)
            logger.info(f"[OpenStack] Red Provider creada: {_created_network.name} (ID: {_created_network.id})")

            # 2. Crear Subnet asociada con CIDR dinámico
            if request.vms:
                first_ip = request.vms[0].internal_ip
                parts = first_ip.split(".")
                cidr = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
                gateway_ip = f"{parts[0]}.{parts[1]}.{parts[2]}.1"
            else:
                cidr = "10.0.1.0/24"
                gateway_ip = "10.0.1.1"

            _created_subnet = await asyncio.to_thread(
                conn.network.create_subnet,
                name=subnet_name,
                network_id=_created_network.id,
                ip_version=4,
                cidr=cidr,
                gateway_ip=gateway_ip
            )
            logger.info(f"[OpenStack] Subnet creada: {_created_subnet.name} (CIDR: {cidr}, GW: {gateway_ip})")

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
                if not ext_subnet:
                    try:
                        ext_subnet = await asyncio.to_thread(
                            conn.network.create_subnet,
                            name=settings.OS_EXTERNAL_SUBNET_NAME,
                            network_id=ext_net.id,
                            ip_version=4,
                            cidr=settings.OS_EXTERNAL_SUBNET_CIDR,
                            gateway_ip=settings.OS_EXTERNAL_GATEWAY_IP,
                            enable_dhcp=True,
                        )
                        logger.info(f"[OpenStack] external_subnet creada: {settings.OS_EXTERNAL_SUBNET_CIDR}")
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
                    logger.info(f"[OpenStack] Router virtual creado: {_created_router.name} (ID: {_created_router.id})")

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
                logger.warning(f"[OpenStack] Red externa '{settings.OS_EXTERNAL_NETWORK_NAME}' no encontrada. Enrutamiento L3 omitido.")

            # 5. Crear puerto Neutron para cada VM (red interna del slice + opcional Floating IP)
            port_map = {}
            for vm in request.vms:
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
                        logger.error(f"[OpenStack] Falló la creación/asociación de Floating IP para VM {vm.vm_id}: {e}")

                port_map[vm.vm_id] = {
                    "provider_port_id": port.id,
                    "external_port_id": None,  # Ya no se requiere un segundo puerto físico virtual adjunto a la VM
                    "external_ip": external_ip,
                    "link_ports": [],  # Inicializar lista de puertos de enlaces
                }

            # 6. Crear Redes, Subredes y Puertos para cada enlace (link) del slice
            for link in request.links:
                vlan_id = link.vlan_id
                vm1_id = link.vm1_id
                vm2_id = link.vm2_id
                logger.info(f"[OpenStack] Configurando enlace {link.connection_id} (VLAN {vlan_id}) entre {vm1_id} y {vm2_id}")

                try:
                    # Crear red para el enlace
                    net_link = await asyncio.to_thread(
                        conn.network.create_network,
                        name=f"net-link-{vlan_id}"
                    )
                    _created_link_networks.append(net_link.id)

                    # Crear subnet /30 sin DHCP para conexión punto a punto
                    link_cidr = f"10.{(vlan_id // 256) % 256}.{vlan_id % 256}.0/30"
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

                    # Crear puerto para VM1 (IP .1) - Port Security desactivado para permitir routing/IPs libres
                    port1 = await asyncio.to_thread(
                        conn.network.create_port,
                        name=f"port-link-{vlan_id}-{vm1_id}",
                        network_id=net_link.id,
                        port_security_enabled=False,
                        fixed_ips=[{"ip_address": f"10.{(vlan_id // 256) % 256}.{vlan_id % 256}.1"}]
                    )
                    _created_ports.append(port1)

                    # Crear puerto para VM2 (IP .2) - Port Security desactivado para permitir routing/IPs libres
                    port2 = await asyncio.to_thread(
                        conn.network.create_port,
                        name=f"port-link-{vlan_id}-{vm2_id}",
                        network_id=net_link.id,
                        port_security_enabled=False,
                        fixed_ips=[{"ip_address": f"10.{(vlan_id // 256) % 256}.{vlan_id % 256}.2"}]
                    )
                    _created_ports.append(port2)

                    # Registrar puertos en el port_map de cada VM
                    if vm1_id in port_map:
                        port_map[vm1_id]["link_ports"].append(port1.id)
                    if vm2_id in port_map:
                        port_map[vm2_id]["link_ports"].append(port2.id)

                    logger.info(f"[OpenStack] Enlace creado exitosamente: {net_link.name}")
                except Exception as e:
                    logger.error(f"[OpenStack] Error creando enlace {link.connection_id}: {e}")
                    raise e

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
        logger.info(f"[OpenStack] Iniciando destrucción de red para slice {slice_id}")
        
        try:
            conn = await asyncio.to_thread(get_connection)
            
            network_name = f"net-slice-{slice_id}"
            subnet_name = f"subnet-slice-{slice_id}"
            secgroup_name = f"secgroup-slice-{slice_id}"
            router_name = f"router-slice-{slice_id}"

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
