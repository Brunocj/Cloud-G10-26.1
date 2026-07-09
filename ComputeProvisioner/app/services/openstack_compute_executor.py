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

    async def get_console_token(self, conn, provider_instance_id: str) -> Optional[str]:
        """
        Solicita a Nova un token de consola noVNC nuevo para una instancia existente.
        Los tokens de nova-novncproxy son de corta duración (~10 min) y de un solo uso,
        por lo que deben pedirse justo antes de abrir la consola, no reusarse del deploy.
        """
        # Nova 22.x (Yoga) usa el endpoint POST /servers/{id}/remote-consoles (mv 2.6+)
        # openstacksdk lo expone como create_server_remote_console.
        # Fallback al action legacy os-getVNCConsole por compatibilidad.
        vnc_url = None
        try:
            console = await asyncio.to_thread(
                conn.compute.create_server_remote_console,
                provider_instance_id,
                **{"protocol": "vnc", "type": "novnc"},
            )
            vnc_url = getattr(console, "url", None) or (console.get("url") if isinstance(console, dict) else None)
        except Exception as e:
            logger.warning(f"[OpenStack] create_server_remote_console falló: {e}. Intentando acción legacy os-getVNCConsole...")
            try:
                resp = await asyncio.to_thread(
                    conn.compute._action,
                    "os-getVNCConsole", provider_instance_id, {"type": "novnc"}
                )
                vnc_url = (resp or {}).get("console", {}).get("url")
            except Exception as e2:
                logger.error(f"[OpenStack] No se pudo obtener consola VNC: {e2}")
                return None

        # Extraer solo el token UUID de la URL de nova-novncproxy
        # URL: http://controller:6080/vnc_auto.html?path=%3Ftoken%3D<UUID>
        if not vnc_url:
            return None
        try:
            parsed = urlparse(vnc_url)
            path_encoded = parse_qs(parsed.query).get("path", [""])[0]
            path_decoded = unquote(path_encoded).lstrip("?")
            return parse_qs(path_decoded).get("token", [None])[0]
        except Exception:
            return None

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

    async def attach_interfaces(self, conn, vm: VMSpec, slice_id: str) -> VMResult:
        """
        Modo Edición (REQ-US-14): conecta en caliente los puertos Neutron de los
        enlaces nuevos a una instancia Nova YA en ejecución (interface-attach).
        """
        link_ports = []
        if vm.network_ports and isinstance(vm.network_ports, dict):
            link_ports = vm.network_ports.get("link_ports", []) or []

        instance_id = vm.provider_instance_id
        try:
            if not instance_id:
                # Fallback: resolver por nombre (slice_name-vm_label)
                safe_slice = (vm.slice_name or str(slice_id)).replace(" ", "-")[:24]
                safe_vm    = (vm.vm_label   or vm.vm_id).replace(" ", "-")[:16]
                server = await asyncio.to_thread(conn.compute.find_server, f"{safe_slice}-{safe_vm}")
                if not server:
                    raise RuntimeError(f"No se encontró la instancia Nova de la VM existente {vm.vm_id}.")
                instance_id = server.id

            for port_id in link_ports:
                await asyncio.to_thread(
                    conn.compute.create_server_interface,
                    instance_id, port_id=port_id,
                )
                logger.info(f"[OpenStack] 🔌 Interface-attach OK: VM {vm.vm_id} ← puerto {port_id}")

            return VMResult(
                vm_id=vm.vm_id, worker_ip=vm.worker_ip,
                vnc_port=vm.vnc_port, provider_instance_id=instance_id,
            )
        except Exception as exc:
            logger.error(f"[OpenStack] ❌ Interface-attach falló para VM {vm.vm_id}: {exc}")
            return VMResult(vm_id=vm.vm_id, worker_ip=vm.worker_ip, error=str(exc))

    async def deploy_vm(self, conn, vm: VMSpec, slice_id: str) -> VMResult:
        """Despliega una VM individual de forma asíncrona en OpenStack."""
        # Modo Edición: VM existente → solo conectar interfaces nuevas
        if getattr(vm, "already_deployed", False):
            return await self.attach_interfaces(conn, vm, slice_id)

        logger.info(f"[OpenStack] Iniciando deploy de VM {vm.vm_id} (CPU: {vm.vcpus}, RAM: {vm.ram_mb} MB, Host: {vm.selected_host})")
        
        try:
            # 1. Resolver Imagen y Flavor
            image_uuid = await asyncio.to_thread(self._resolve_image_uuid, conn, vm.image_path)
            flavor_uuid = await asyncio.to_thread(self._find_flavor_uuid, conn, vm.vcpus, vm.ram_mb)
            
            # 2. Extraer ID de Puerto Neutron
            port_id = None
            external_port_id = None
            external_ip = None
            link_ports = []
            if vm.network_ports and isinstance(vm.network_ports, dict):
                port_id = vm.network_ports.get("provider_port_id")
                external_port_id = vm.network_ports.get("external_port_id")
                external_ip = vm.network_ports.get("external_ip")
                link_ports = vm.network_ports.get("link_ports", [])
            elif vm.network_ports and isinstance(vm.network_ports, list) and vm.network_ports:
                port_id = vm.network_ports[0]

            if not port_id:
                raise RuntimeError("No se especificó un puerto Neutron válido en network_ports.")

            # 3. Crear Instancia en Nova aplicando BYOS (scheduler bypass)
            safe_slice = (vm.slice_name or str(slice_id)).replace(" ", "-")[:24]
            safe_vm    = (vm.vm_label   or vm.vm_id).replace(" ", "-")[:16]
            server_name = f"{safe_slice}-{safe_vm}"

            networks = [{"port": port_id}]
            if external_port_id:
                networks.append({"port": external_port_id})
                logger.info(f"[OpenStack] VM {vm.vm_id}: adjuntando puerto externo {external_port_id} (ip={external_ip})")
            
            for lp_id in link_ports:
                networks.append({"port": lp_id})
                logger.info(f"[OpenStack] VM {vm.vm_id}: adjuntando puerto de enlace {lp_id}")

            create_kwargs = dict(
                name=server_name,
                image_id=image_uuid,
                flavor_id=flavor_uuid,
                networks=networks,
            )

            # Llave pública del dueño (REQ-US-02): inyectada vía cloud-init
            # user_data. Solo surte efecto en imágenes con soporte cloud-init
            # (CirrOS la ignora sin romper el arranque).
            owner_key = getattr(vm, "owner_ssh_public_key", None)
            if owner_key:
                import base64
                cloud_cfg = f"#cloud-config\nssh_authorized_keys:\n  - {owner_key}\n"
                create_kwargs["user_data"] = base64.b64encode(cloud_cfg.encode()).decode()
                logger.info(f"[OpenStack] VM {vm.vm_id}: llave SSH del dueño inyectada vía user_data")

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
            vnc_token = await self.get_console_token(conn, server.id)
            logger.info(f"[OpenStack] VM {vm.vm_id} desplegada exitosamente")
            logger.info(f"[OpenStack] vnc_token extraído: {vnc_token!r}")
            result = VMResult(
                vm_id=vm.vm_id,
                worker_ip=vm.worker_ip,
                vnc_port=vm.vnc_port,
                provider_instance_id=server.id,
                vnc_url=vnc_token,
                external_ip=external_ip,
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

    async def detach_interface(self, conn, unplug: dict, slice_id: str) -> None:
        """
        Modo Edición eliminación (REQ-US-14): desconecta en caliente el puerto
        de un enlace eliminado de una instancia Nova sobreviviente. El puerto se
        localiza por nombre (port-link-{vlan}-{vm_id}); Neutron lo detach-ea y
        luego se elimina. Best-effort.
        """
        vm_id      = unplug.get("vm_id")
        vlan_id    = unplug.get("vlan_id")
        instance_id = unplug.get("provider_instance_id")
        port_name  = f"port-link-{vlan_id}-{vm_id}"
        try:
            port = await asyncio.to_thread(conn.network.find_port, port_name)
            if not port:
                logger.info(f"[OpenStack] detach: puerto {port_name} no existe (ya limpio)")
                return
            if instance_id:
                try:
                    await asyncio.to_thread(conn.compute.delete_server_interface, port.id, instance_id)
                    logger.info(f"[OpenStack] ✂ Interface-detach: VM {vm_id} ⊘ puerto {port.id}")
                except Exception as e:
                    logger.warning(f"[OpenStack] detach del puerto {port.id} en {instance_id} falló: {e}")
        except Exception as exc:
            logger.warning(f"[OpenStack] detach_interface error para VM {vm_id}: {exc}")

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
