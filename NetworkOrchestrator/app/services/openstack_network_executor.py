import os
import logging
import asyncio
import openstack
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
        _created_router    = None
        _created_ports     = []

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

            # 4. Router virtual hacia red externa
            ext_net_name = os.getenv("OPENSTACK_EXTERNAL_NETWORK", "ext-net")
            ext_net = await asyncio.to_thread(conn.network.find_network, ext_net_name)
            if ext_net:
                _created_router = await asyncio.to_thread(
                    conn.network.create_router,
                    name=router_name,
                    external_gateway_info={"network_id": ext_net.id}
                )
                logger.info(f"[OpenStack] Router virtual creado: {_created_router.name} (ID: {_created_router.id})")
                await asyncio.to_thread(
                    conn.network.add_interface_to_router,
                    _created_router.id,
                    subnet_id=_created_subnet.id
                )
                logger.info(f"[OpenStack] Subnet conectada al Router virtual")
            else:
                logger.warning(f"[OpenStack] Red externa '{ext_net_name}' no encontrada. Salida a Internet omitida.")

            # 5. Crear puerto Neutron para cada VM
            port_map = {}
            for vm in request.vms:
                port = await asyncio.to_thread(
                    conn.network.create_port,
                    name=f"port-{slice_id}-{vm.vm_id}",
                    network_id=_created_network.id,
                    fixed_ips=[{"ip_address": vm.internal_ip}],
                    security_groups=[_created_sec_group.id]
                )
                _created_ports.append(port)
                logger.info(f"[OpenStack] Puerto Neutron creado para VM {vm.vm_id} con IP fija: {vm.internal_ip}")

                floating_ip = None
                floating_ip_id = None
                if (vm.internet_access == 1 or vm.external_ip) and ext_net and _created_router:
                    try:
                        fip = await asyncio.to_thread(
                            conn.network.create_ip,
                            floating_network_id=ext_net.id,
                            port_id=port.id
                        )
                        floating_ip = fip.floating_ip_address
                        floating_ip_id = fip.id
                        logger.info(f"[OpenStack] IP flotante asociada a VM {vm.vm_id}: {floating_ip}")
                    except Exception as e:
                        logger.error(f"[OpenStack] Falló la asignación de IP flotante a VM {vm.vm_id}: {e}")

                port_map[vm.vm_id] = {
                    "provider_port_id": port.id,
                    "floating_ip": floating_ip,
                    "floating_ip_id": floating_ip_id
                }

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
            
            network = await asyncio.to_thread(conn.network.find_network, network_name)
            subnet = await asyncio.to_thread(conn.network.find_subnet, subnet_name)
            router = await asyncio.to_thread(conn.network.find_router, router_name)
            
            # 1. Borrar IPs Flotantes y Puertos
            if network:
                ports = list(await asyncio.to_thread(conn.network.ports, network_id=network.id))
                for port in ports:
                    fips = list(await asyncio.to_thread(conn.network.ips, port_id=port.id))
                    for fip in fips:
                        logger.info(f"[OpenStack] Liberando IP flotante: {fip.floating_ip_address}")
                        await asyncio.to_thread(conn.network.delete_ip, fip.id)
                        
                    logger.info(f"[OpenStack] Eliminando puerto Neutron: {port.id}")
                    await asyncio.to_thread(conn.network.delete_port, port.id)
            
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
                logger.info(f"[OpenStack] Eliminando router virtual: {router.name}")
                await asyncio.to_thread(conn.network.delete_router, router.id)
                
            # 3. Eliminar Subred
            if subnet:
                logger.info(f"[OpenStack] Eliminando subnet: {subnet.name}")
                await asyncio.to_thread(conn.network.delete_subnet, subnet.id)
                
            # 4. Eliminar Red
            if network:
                logger.info(f"[OpenStack] Eliminando red: {network.name}")
                await asyncio.to_thread(conn.network.delete_network, network.id)
                
            # 5. Eliminar Security Group
            sec_group = await asyncio.to_thread(conn.network.find_security_group, secgroup_name)
            if sec_group:
                logger.info(f"[OpenStack] Eliminando Security Group: {sec_group.name}")
                await asyncio.to_thread(conn.network.delete_security_group, sec_group.id)
                
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
