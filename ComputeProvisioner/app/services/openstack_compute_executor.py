import os
import logging
import asyncio
import openstack
from typing import Optional, List
from urllib.parse import urlparse, parse_qs, unquote
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
            safe_slice = (vm.slice_name or str(slice_id)).replace(" ", "-")[:24]
            safe_vm    = (vm.vm_label   or vm.vm_id).replace(" ", "-")[:16]
            server_name = f"{safe_slice}-{safe_vm}"

            create_kwargs = dict(
                name=server_name,
                image_id=image_uuid,
                flavor_id=flavor_uuid,
                networks=[{"port": port_id}],
            )

            byos_enabled = os.getenv("OS_BYOS_FORCE_HOST", "true").lower() in ("1", "true", "yes")
            if vm.selected_host and byos_enabled:
                # availability_zone=nova:<host> le indica a Nova que bypass el scheduler
                # y construya directamente en ese compute host.
                create_kwargs["availability_zone"] = f"nova:{vm.selected_host}"
                logger.info(f"[OpenStack] BYOS: forzando host '{vm.selected_host}' via availability_zone")
            else:
                logger.info(f"[OpenStack] BYOS deshabilitado — Nova scheduler elige host libremente")

            server = await asyncio.to_thread(conn.compute.create_server, **create_kwargs)
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
            # Nova 22.x (Yoga) usa el endpoint POST /servers/{id}/remote-consoles (mv 2.6+)
            # openstacksdk lo expone como create_server_remote_console.
            # Fallback al action legacy os-getVNCConsole por compatibilidad.
            vnc_url = None
            try:
                # Nova API: POST /servers/{id}/remote-consoles body={"remote_console":{"protocol":"vnc","type":"novnc"}}
                # openstacksdk mapea 'type' (no 'console_type') al campo JSON 'type'
                console = await asyncio.to_thread(
                    conn.compute.create_server_remote_console,
                    server.id,
                    **{"protocol": "vnc", "type": "novnc"},
                )
                vnc_url = getattr(console, "url", None) or (console.get("url") if isinstance(console, dict) else None)
            except Exception as e:
                logger.warning(f"[OpenStack] create_server_remote_console falló: {e}. Intentando acción legacy os-getVNCConsole...")
                try:
                    # Fallback: POST /servers/{id}/action {"os-getVNCConsole": {"type": "novnc"}}
                    resp = await asyncio.to_thread(
                        conn.compute._action,
                        "os-getVNCConsole", server.id, {"type": "novnc"}
                    )
                    vnc_url = (resp or {}).get("console", {}).get("url")
                except Exception as e2:
                    logger.error(f"[OpenStack] No se pudo obtener consola VNC: {e2}")
                    
            # Extraer solo el token UUID de la URL de nova-novncproxy
            # URL: http://controller:6080/vnc_auto.html?path=%3Ftoken%3D<UUID>
            vnc_token = None
            if vnc_url:
                try:
                    parsed = urlparse(vnc_url)
                    path_encoded = parse_qs(parsed.query).get("path", [""])[0]
                    path_decoded = unquote(path_encoded).lstrip("?")
                    vnc_token = parse_qs(path_decoded).get("token", [None])[0]
                except Exception:
                    pass
            logger.info(f"[OpenStack] VM {vm.vm_id} desplegada exitosamente")
            logger.info(f"[OpenStack] vnc_url raw: {vnc_url!r}")
            logger.info(f"[OpenStack] vnc_token extraído: {vnc_token!r}")
            result = VMResult(
                vm_id=vm.vm_id,
                worker_ip=vm.worker_ip,
                vnc_port=vm.vnc_port,
                provider_instance_id=server.id,
                vnc_url=vnc_token,
            )
            logger.info(f"[OpenStack] VMResult.vnc_url = {result.vnc_url!r}")
            return result
            
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
                # Intentar con el nombre nuevo (slice_name-vm_label) y el legado
                slice_name = vm_record.get("slice_name") or str(slice_id)
                vm_label   = vm_record.get("vm_label")   or vm_id
                safe_slice = slice_name.replace(" ", "-")[:24]
                safe_vm    = vm_label.replace(" ", "-")[:16]
                for candidate in [f"{safe_slice}-{safe_vm}", f"vm-slice-{slice_id}-{vm_id}"]:
                    server = await asyncio.to_thread(conn.compute.find_server, candidate)
                    if server:
                        break
                
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
