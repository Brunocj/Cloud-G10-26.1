import os
import re
import logging
import asyncio
import openstack
from typing import Optional, List
from urllib.parse import urlparse, parse_qs, unquote
from app.models.schemas import VMSpec, VMResult, DeployStatus
from app.services.ssh_client import SSHClient

logger = logging.getLogger("compute-provisioner.openstack")


def _sanitize_flavor_name(name: str) -> str:
    """Nombre legible para Nova: solo alfanuméricos/._- , máx 60 chars."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-")
    return cleaned[:60] or "flavor"

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

    def _ensure_flavor_uuid(self, conn, vcpus: int, ram_mb: int, disk_gb: int, cached_flavor_id: str = None, flavor_name: str = None) -> str:
        """
        Materializa un flavor Nova con specs EXACTOS (estrategia BYOS-flavors).

        Como usamos credenciales admin, en vez de "el más cercano" garantizamos
        que Nova tenga un flavor idéntico a lo pedido: si ya existe uno con
        (vcpus, ram, disk) exactos lo reutiliza; si no, lo crea al vuelo. Así la
        VM sale con los recursos exactos que el usuario eligió en su flavor.

        Si `flavor_name` viene informado (la VM usa un flavor lógico con
        nombre propio), se prioriza crearlo/reutilizarlo con ese nombre para
        que sea reconocible en `openstack flavor list`, en vez de colapsarlo
        por specs con otro flavor de nombre distinto.
        """
        vcpus   = int(vcpus)
        ram_mb  = int(round(float(ram_mb)))
        disk_gb = int(round(float(disk_gb))) if disk_gb else 1

        # 0. UUID cacheado (flavor lógico materializado eager por un admin, o
        #    ya resuelto en un deploy previo) — evita listar todos los flavors
        #    de Nova si ya sabemos cuál es. Se valida que siga existiendo y con
        #    los specs correctos por si fue borrado/editado fuera de la plataforma.
        if cached_flavor_id:
            try:
                cached = conn.compute.get_flavor(cached_flavor_id)
                if (cached.vcpus == vcpus and int(cached.ram) == ram_mb
                        and int(getattr(cached, "disk", 0) or 0) == disk_gb):
                    return cached.id
                logger.warning(
                    f"[OpenStack] provider_flavor_id cacheado {cached_flavor_id} tiene specs distintos "
                    f"a los pedidos — recalculando."
                )
            except Exception:
                logger.warning(f"[OpenStack] provider_flavor_id cacheado {cached_flavor_id} ya no existe en Nova — recalculando.")

        # 1. Nombre propio → crear/reutilizar directo con ese nombre
        if flavor_name:
            base_name = _sanitize_flavor_name(flavor_name)
            for candidate in (base_name, f"{base_name}-{vcpus}c{ram_mb}m{disk_gb}g"):
                try:
                    created = conn.compute.create_flavor(
                        name=candidate, vcpus=vcpus, ram=ram_mb, disk=disk_gb, is_public=True,
                    )
                    logger.info(f"[OpenStack] Flavor Nova creado al vuelo: {candidate} (UUID={created.id})")
                    return created.id
                except Exception:
                    existing = next((f for f in conn.compute.flavors() if f.name == candidate), None)
                    if (existing and existing.vcpus == vcpus and int(existing.ram) == ram_mb
                            and int(getattr(existing, "disk", 0) or 0) == disk_gb):
                        return existing.id
            logger.warning(f"[OpenStack] No se pudo crear el flavor Nova con nombre '{base_name}' — usando fallback genérico.")

        # 2. Coincidencia exacta (cpu + ram + disco)
        for flavor in conn.compute.flavors():
            if (flavor.vcpus == vcpus and int(flavor.ram) == ram_mb
                    and int(getattr(flavor, "disk", 0) or 0) == disk_gb):
                return flavor.id

        # 3. No existe → crearlo con specs exactos (idempotente por nombre)
        name = f"pucp-{vcpus}c-{ram_mb}m-{disk_gb}g"
        try:
            created = conn.compute.create_flavor(
                name=name, vcpus=vcpus, ram=ram_mb, disk=disk_gb, is_public=True,
            )
            logger.info(f"[OpenStack] Flavor Nova creado al vuelo: {name} (UUID={created.id})")
            return created.id
        except Exception as exc:
            # Carrera: otro deploy lo pudo crear en paralelo → reintentar la búsqueda por nombre
            for flavor in conn.compute.flavors():
                if flavor.name == name:
                    return flavor.id
            raise RuntimeError(f"No se pudo crear/encontrar el flavor Nova '{name}': {exc}")

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
            flavor_uuid = await asyncio.to_thread(
                self._ensure_flavor_uuid, conn, vm.vcpus, vm.ram_mb, getattr(vm, "disk_gb", 1),
                getattr(vm, "provider_flavor_id", None), getattr(vm, "flavor_name", None),
            )
            
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

            # Cloud-init user_data (REQ-US-02): inyecta credenciales de la VM
            # (vm_user / vm_password) y la llave SSH del dueño del slice.
            # Replica la lógica de qemu_executor._prepare_cloud_init para que la
            # UX sea consistente entre zonas Linux Cluster y OpenStack.
            # Solo surte efecto en imágenes con soporte cloud-init
            # (CirrOS la ignora sin romper el arranque).
            import base64
            owner_key   = getattr(vm, "owner_ssh_public_key", None) or ""
            vm_user     = getattr(vm, "vm_user", None) or "ubuntu"
            vm_password = getattr(vm, "vm_password", None) or "pucp2026"

            # Usuario por defecto de la imagen base
            default_image_user = "cirros" if "cirros" in (vm.image_path or "").lower() else "ubuntu"

            # Bloque users: siempre el default de la imagen; si el custom es
            # distinto, se agrega como usuario adicional con sudo NOPASSWD.
            users_block = "users:\n  - default\n"
            if vm_user != default_image_user:
                # lock_passwd: false es CLAVE — sin esto cloud-init crea el
                # usuario bloqueado y no acepta login por password en la
                # consola noVNC/tty, aunque chpasswd le haya seteado el pwd
                # y ssh_pwauth esté activo (ssh_pwauth solo aplica a SSH).
                users_block += (
                    f"  - name: {vm_user}\n"
                    f"    lock_passwd: false\n"
                    f"    sudo: ['ALL=(ALL) NOPASSWD:ALL']\n"
                    f"    groups: sudo\n"
                    f"    shell: /bin/bash\n"
                )
                if owner_key:
                    users_block += (
                        f"    ssh_authorized_keys:\n"
                        f"      - {owner_key}\n"
                    )

            # Llave del dueño también para el usuario default de la imagen
            owner_keys_block = ""
            if owner_key:
                owner_keys_block = (
                    f"ssh_authorized_keys:\n"
                    f"  - {owner_key}\n"
                )

            # chpasswd: password para el usuario default y para el custom si aplica
            chpasswd_list = f"{default_image_user}:{vm_password}"
            if vm_user != default_image_user:
                chpasswd_list += f"\n    {vm_user}:{vm_password}"

            cloud_cfg = (
                "#cloud-config\n"
                "ssh_pwauth: true\n"
                f"{users_block}"
                f"{owner_keys_block}"
                "chpasswd:\n"
                "  list: |\n"
                f"    {chpasswd_list}\n"
                "  expire: False\n"
            )
            create_kwargs["user_data"] = base64.b64encode(cloud_cfg.encode()).decode()
            logger.info(
                f"[OpenStack] VM {vm.vm_id}: cloud-init inyectado "
                f"(user='{vm_user}', default_image_user='{default_image_user}', "
                f"owner_key={'sí' if owner_key else 'no'})"
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
                provider_flavor_id=flavor_uuid,
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

    async def _qinq_ovs_cmds(self, s_vlan: int, cvlans_sp: str) -> str:
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

    async def apply_qinq(self, conn, slice_id: str, s_vlan: int,
                         deployed_results: list, compute_ssh_map: dict) -> None:
        """
        Q-in-Q en OpenStack (SSH a los computes, post-Neutron). Interpone un
        `br-qinq` con un puerto dot1q-tunnel que empuja el S-VID del slice sobre
        los C-VIDs (segmentation_ids que Neutron asignó a las redes de enlace).
        Se aplica en cada compute donde Nova REALMENTE puso una VM del slice
        (fuente de verdad = Nova, no el worker_id de la BD).
        """
        if not s_vlan or not compute_ssh_map:
            return
        hosts, cvlans = set(), set()
        for r in deployed_results:
            if getattr(r, "error", None) or not getattr(r, "provider_instance_id", None):
                continue
            try:
                srv = await asyncio.to_thread(conn.compute.get_server, r.provider_instance_id)
                host = getattr(srv, "compute_host", None) or getattr(srv, "hypervisor_hostname", None)
                if host:
                    hosts.add(host)
                ifaces = await asyncio.to_thread(lambda: list(conn.compute.server_interfaces(srv.id)))
                for iface in ifaces:
                    net = await asyncio.to_thread(conn.network.get_network, iface.net_id)
                    seg  = getattr(net, "provider_segmentation_id", None)
                    name = getattr(net, "name", "") or ""
                    if seg and name.startswith("net-link-"):
                        cvlans.add(int(seg))
            except Exception as exc:
                logger.warning(f"[OpenStack][QinQ] No se pudo leer host/segids de {r.vm_id}: {exc}")

        if not hosts:
            logger.warning(f"[OpenStack][QinQ] slice {slice_id}: sin hosts — omitido")
            return
        cvlans_sp = " ".join(str(v) for v in sorted(cvlans))
        cmd = await self._qinq_ovs_cmds(s_vlan, cvlans_sp)

        for host in hosts:
            creds = compute_ssh_map.get(host)
            if not creds:
                logger.error(f"[OpenStack][QinQ] Sin SSH para compute '{host}' — omitido (¿Worker.name == host de Nova?)")
                continue
            try:
                def _run():
                    with SSHClient(creds["ip"], creds["user"], creds.get("key", ""),
                                   port=int(creds.get("port", 22))) as ssh:
                        return ssh.exec(cmd)
                ec, _, err = await asyncio.to_thread(_run)
                if ec != 0:
                    logger.error(f"[OpenStack][QinQ] Fallo en compute '{host}': {err}")
                else:
                    logger.info(f"[OpenStack][QinQ] ✅ compute '{host}': S-VID {s_vlan} sobre C-VIDs [{cvlans_sp}]")
            except Exception as exc:
                logger.error(f"[OpenStack][QinQ] Error SSH a '{host}': {exc}")

    async def teardown_qinq(self, slice_id: str, s_vlan: int, compute_ssh_map: dict) -> None:
        """Quita el patch dot1q-tunnel del slice en todos los computes (destroy)."""
        if not s_vlan or not compute_ssh_map:
            return
        pi, pq = f"pi-{s_vlan}", f"pq-{s_vlan}"
        cmd = (f"sudo ovs-vsctl --if-exists del-port br-vlan {pi}; "
               f"sudo ovs-vsctl --if-exists del-port br-qinq {pq}")
        for host, creds in compute_ssh_map.items():
            try:
                def _run():
                    with SSHClient(creds["ip"], creds["user"], creds.get("key", ""),
                                   port=int(creds.get("port", 22))) as ssh:
                        return ssh.exec(cmd)
                await asyncio.to_thread(_run)
            except Exception as exc:
                logger.warning(f"[OpenStack][QinQ] teardown en '{host}' falló: {exc}")

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
