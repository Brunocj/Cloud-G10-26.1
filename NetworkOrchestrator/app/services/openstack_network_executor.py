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
        
        try:
            conn = await asyncio.to_thread(get_connection)
            
            network_name = f"net-slice-{slice_id}"
            subnet_name = f"subnet-slice-{slice_id}"
            secgroup_name = f"secgroup-slice-{slice_id}"
            router_name = f"router-slice-{slice_id}"
            
            # 1. Crear Provider Network (R4: cero overhead, vlan_transparent=True)
            network_args = {
                "name": network_name,
                "vlan_transparent": True,
            }
            # Parámetros del proveedor configurables vía variables de entorno
            prov_type = os.getenv("OS_PROVIDER_NETWORK_TYPE")
            if prov_type:
                network_args["provider_network_type"] = prov_type
            prov_phys = os.getenv("OS_PROVIDER_PHYSICAL_NETWORK")
            if prov_phys:
                network_args["provider_physical_network"] = prov_phys
            prov_seg = os.getenv("OS_PROVIDER_SEGMENTATION_ID")
            if prov_seg:
                network_args["provider_segmentation_id"] = int(prov_seg)
                
            network = await asyncio.to_thread(conn.network.create_network, **network_args)
            logger.info(f"[OpenStack] Red Provider creada: {network.name} (ID: {network.id})")
            
            # 2. Crear Subnet asociada con CIDR dinámico
            if request.vms:
                first_ip = request.vms[0].internal_ip
                parts = first_ip.split(".")
                cidr = f"{parts[0]}.{parts[1]}.{parts[2]}.0/24"
                gateway_ip = f"{parts[0]}.{parts[1]}.{parts[2]}.1"
            else:
                cidr = "10.0.1.0/24"
                gateway_ip = "10.0.1.1"
                
            subnet = await asyncio.to_thread(
                conn.network.create_subnet,
                name=subnet_name,
                network_id=network.id,
                ip_version=4,
                cidr=cidr,
                gateway_ip=gateway_ip
            )
            logger.info(f"[OpenStack] Subnet creada: {subnet.name} (CIDR: {cidr}, GW: {gateway_ip})")
            
            # 3. Crear Security Group dedicado (R5: Seguridad)
            sec_group = await asyncio.to_thread(
                conn.network.create_security_group,
                name=secgroup_name,
                description=f"Security group for slice {slice_id}"
            )
            logger.info(f"[OpenStack] Security Group creado: {sec_group.name} (ID: {sec_group.id})")
            
            # Reglas ingress: ICMP (Ping)
            await asyncio.to_thread(
                conn.network.create_security_group_rule,
                security_group_id=sec_group.id,
                direction="ingress",
                protocol="icmp",
                ethertype="IPv4"
            )
            # Reglas ingress: SSH (TCP 22)
            await asyncio.to_thread(
                conn.network.create_security_group_rule,
                security_group_id=sec_group.id,
                direction="ingress",
                protocol="tcp",
                port_range_min=22,
                port_range_max=22,
                ethertype="IPv4"
            )
            # Egress (Salida IPv4/IPv6) se crea automáticamente por Neutron en nuevos grupos de seguridad
            
            # 4. Configurar Router virtual conectado a la red externa (ext-net) para salida a Internet (R5)
            ext_net_name = os.getenv("OS_EXTERNAL_NETWORK", "ext-net")
            ext_net = await asyncio.to_thread(conn.network.find_network, ext_net_name)
            router = None
            if ext_net:
                router = await asyncio.to_thread(
                    conn.network.create_router,
                    name=router_name,
                    external_gateway_info={"network_id": ext_net.id}
                )
                logger.info(f"[OpenStack] Router virtual creado: {router.name} (ID: {router.id})")
                
                await asyncio.to_thread(
                    conn.network.add_interface_to_router,
                    router.id,
                    subnet_id=subnet.id
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
                    network_id=network.id,
                    fixed_ips=[{"ip_address": vm.internal_ip}],
                    security_group_ids=[sec_group.id]
                )
                logger.info(f"[OpenStack] Puerto Neutron creado para VM {vm.vm_id} con IP fija: {vm.internal_ip}")
                
                # Asignar IP flotante si internet_access == 1 (o por defecto para cumplir R5)
                floating_ip = None
                floating_ip_id = None
                if (vm.internet_access == 1 or vm.external_ip) and ext_net and router:
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
