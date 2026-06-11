import os
import logging
import asyncio
import openstack
from typing import Optional, List
from app.models.schemas import VMSpec, VMResult, DeployStatus

logger = logging.getLogger("compute-provisioner.openstack")

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

class OpenStackComputeExecutor:
    """Implementa el aprovisionamiento de cómputo para la zona OpenStack (Strategy Pattern)."""

    def _resolve_image_uuid(self, conn, image_path: str) -> str:
        """Resuelve el nombre o ruta del archivo de imagen a un UUID de Glance."""
        base_name = os.path.basename(image_path)
        name_no_ext = os.path.splitext(base_name)[0]
        
        # 1. Coincidencia exacta de archivo
        img = conn.image.find_image(base_name)
        if img:
            return img.id
            
        # 2. Coincidencia por nombre sin extensión
        img = conn.image.find_image(name_no_ext)
        if img:
            return img.id
            
        # 3. Coincidencia por la ruta original
        img = conn.image.find_image(image_path)
        if img:
            return img.id
            
        # 4. Coincidencia parcial por subcadena
        for glance_img in conn.image.images():
            if base_name.lower() in glance_img.name.lower() or name_no_ext.lower() in glance_img.name.lower():
                return glance_img.id
                
        raise RuntimeError(f"No se pudo encontrar ninguna imagen en Glance que coincida con: {image_path}")

    def _find_flavor_uuid(self, conn, requested_vcpus: int, requested_ram_mb: int) -> str:
        """Busca el flavor adecuado en Nova según los requisitos de CPU y RAM."""
        # 1. Buscar coincidencia exacta
        for flavor in conn.compute.flavors():
            if flavor.vcpus == requested_vcpus and flavor.ram == requested_ram_mb:
                return flavor.id
                
        # 2. Pequeño flavor que satisfaga los requisitos mínimos (vcpus >= req y ram >= req)
        candidate = None
        for flavor in conn.compute.flavors():
            if flavor.vcpus >= requested_vcpus and flavor.ram >= requested_ram_mb:
                if candidate is None or (flavor.vcpus < candidate.vcpus or (flavor.vcpus == candidate.vcpus and flavor.ram < candidate.ram)):
                    candidate = flavor
        if candidate:
            return candidate.id
            
        # 3. Fallback al primer flavor disponible
        flavors = list(conn.compute.flavors())
        if flavors:
            return flavors[0].id
            
        raise RuntimeError(f"No se encontró un Flavor Nova adecuado para vCPUs={requested_vcpus}, RAM={requested_ram_mb} MB")

    async def deploy_vm(self, conn, vm: VMSpec, slice_id: str) -> VMResult:
        """Despliega una VM individual de forma asíncrona en OpenStack."""
        logger.info(f"[OpenStack] Iniciando deploy de VM {vm.vm_id} (CPU: {vm.vcpus}, RAM: {vm.ram_mb} MB, Host: {vm.selected_host})")
        
        try:
            # 1. Resolver Imagen y Flavor
            image_uuid = await asyncio.to_thread(self._resolve_image_uuid, conn, vm.image_path)
            flavor_uuid = await asyncio.to_thread(self._find_flavor_uuid, conn, vm.vcpus, vm.ram_mb)
            
            # 2. Extraer ID de Puerto Neutron
            port_id = None
            if vm.network_ports and isinstance(vm.network_ports, dict):
                port_id = vm.network_ports.get("provider_port_id")
            elif vm.network_ports and isinstance(vm.network_ports, list) and vm.network_ports:
                port_id = vm.network_ports[0]
                
            if not port_id:
                raise RuntimeError("No se especificó un puerto Neutron válido en network_ports.")
                
            # 3. Crear Instancia en Nova aplicando BYOS (scheduler bypass)
            server_name = f"vm-slice-{slice_id}-{vm.vm_id}"
            
            # scheduler hints para forzar host
            availability_zone = f"nova:{vm.selected_host}" if vm.selected_host else None
            
            server = await asyncio.to_thread(
                conn.compute.create_server,
                name=server_name,
                image_id=image_uuid,
                flavor_id=flavor_uuid,
                availability_zone=availability_zone,
                networks=[{"port": port_id}]
            )
            logger.info(f"[OpenStack] Instancia '{server_name}' creada en Nova (UUID: {server.id}). Esperando estado ACTIVE...")
            
            # 4. Polling Asíncrono Pasivo (BUILD -> ACTIVE)
            while True:
                srv = await asyncio.to_thread(conn.compute.get_server, server.id)
                status = srv.status
                logger.info(f"[OpenStack] Estado de VM {vm.vm_id}: {status}")
                if status == "ACTIVE":
                    break
                elif status == "ERROR":
                    fault = getattr(srv, "fault", "Error de aprovisionamiento desconocido")
                    raise RuntimeError(f"La instancia OpenStack entró en estado ERROR: {fault}")
                await asyncio.sleep(5)
                
            # 5. Obtener Consola noVNC
            vnc_url = None
            try:
                console = await asyncio.to_thread(conn.compute.get_vnc_console, server.id, console_type="novnc")
                vnc_url = console.url if hasattr(console, "url") else console.get("url")
            except Exception as e:
                logger.warning(f"[OpenStack] No se pudo obtener la consola VNC estándar: {e}. Intentando fallback a create_console...")
                try:
                    console = await asyncio.to_thread(conn.compute.create_console, server.id, console_type="novnc")
                    vnc_url = console.url if hasattr(console, "url") else console.get("url")
                except Exception as e2:
                    logger.error(f"[OpenStack] Error al obtener consola VNC (fallback): {e2}")
                    
            logger.info(f"[OpenStack] VM {vm.vm_id} desplegada exitosamente (VNC: {vnc_url})")
            return VMResult(
                vm_id=vm.vm_id,
                worker_ip=vm.worker_ip,
                vnc_port=vm.vnc_port,
                provider_instance_id=server.id,
                vnc_url=vnc_url
            )
            
        except Exception as exc:
            logger.error(f"[OpenStack] Error desplegando VM {vm.vm_id}: {exc}", exc_info=True)
            return VMResult(
                vm_id=vm.vm_id,
                worker_ip=vm.worker_ip,
                error=str(exc)
            )

    async def destroy_vm(self, conn, vm_record: dict, slice_id: str) -> Optional[str]:
        """Destruye una VM individual en OpenStack."""
        vm_id = vm_record.get("vm_id")
        provider_instance_id = vm_record.get("provider_instance_id")
        logger.info(f"[OpenStack] Iniciando destrucción de VM {vm_id} (UUID: {provider_instance_id})")
        
        try:
            server = None
            if provider_instance_id:
                server = await asyncio.to_thread(conn.compute.find_server, provider_instance_id)
            if not server:
                server_name = f"vm-slice-{slice_id}-{vm_id}"
                server = await asyncio.to_thread(conn.compute.find_server, server_name)
                
            if server:
                logger.info(f"[OpenStack] Eliminando servidor Nova: {server.name or server.id}")
                await asyncio.to_thread(conn.compute.delete_server, server.id)
                
                # Polling pasivo esperando la eliminación de la VM
                for _ in range(12): # Max 60 seconds
                    srv = await asyncio.to_thread(conn.compute.find_server, server.id)
                    if not srv:
                        logger.info(f"[OpenStack] Instancia {server.id} eliminada de OpenStack.")
                        break
                    await asyncio.sleep(5)
            else:
                logger.info(f"[OpenStack] Servidor {vm_id} ya no existe en OpenStack. Destrucción omitida.")
            return None
        except Exception as exc:
            logger.error(f"[OpenStack] Error destruyendo VM {vm_id} en OpenStack: {exc}")
            return str(exc)
