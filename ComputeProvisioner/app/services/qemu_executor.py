"""
Ejecutor QEMU/KVM sobre workers remotos vía SSH.

Responsabilidades:
  - Crear disco QCOW2 con backing file (thin provisioning).
  - Crear interfaces TAP y conectarlas al bridge OVS (br-int).
  - Lanzar proceso QEMU/KVM con las TAPs y MACs asignadas.
  - Obtener el PID del proceso lanzado.
  - Destruir VMs: matar proceso, eliminar disco, limpiar TAPs.
"""

import logging
from typing import List

from app.core.config import settings
from app.models.schemas import TapInterface
from app.services.ssh_client import SSHClient
from app.utils.image_resolver import get_image_path, get_vm_disk_path

logger = logging.getLogger(__name__)


class QEMUExecutor:
    """
    Ejecuta operaciones QEMU/KVM en un worker remoto.
    Cada instancia opera sobre un único worker (una conexión SSH).
    """

    def __init__(self, ssh: SSHClient):
        self._ssh = ssh

    def _exec_checked(self, command: str) -> str:
        """Ejecuta un comando y lanza excepción si el exit code != 0."""
        code, out, err = self._ssh.exec(command)
        if code != 0:
            raise RuntimeError(
                f"Comando falló (exit {code}):\n  cmd: {command}\n  stderr: {err}"
            )
        return out

    # ------------------------------------------------------------------
    # Deploy
    # ------------------------------------------------------------------

    def create_disk(self, vm_id: str, slice_id: str, image_path: str, worker_ip: str, disk_gb: float) -> str:
        """
        Crea un disco QCOW2 con thin provisioning (backing file).

        Es idempotente: si el disco ya existe (por un intento previo fallido o una
        VM que sigue corriendo), lo elimina primero para evitar el error
        "Failed to get write lock" en escenarios de alta concurrencia.
        """
        disk_path = get_vm_disk_path(vm_id, slice_id)

        # --- Idempotencia: eliminar disco previo si existe ---
        try:
            _code, check_out, _ = self._ssh.exec(f"test -f {disk_path} && echo EXISTS || echo MISSING")
            if "EXISTS" in check_out:
                logger.warning(
                    "Disco previo encontrado en %s — eliminando antes de recrear (retry idempotente)",
                    disk_path
                )
                self._exec_checked(f"sudo rm -f {disk_path}")
        except Exception as exc:
            logger.warning("No se pudo verificar existencia de disco previo en %s: %s", disk_path, exc)


        # Consultamos el tamaño virtual de la imagen base (en bytes)
        requested_mb = int(disk_gb * 1024)
        try:
            out = self._exec_checked(
                f"sudo qemu-img info --output=json {image_path}"
            )
            import json
            backing_info = json.loads(out)
            backing_bytes = backing_info.get("virtual-size", 0)
            backing_mb = int(backing_bytes / (1024 * 1024))
            logger.info(
                "Imagen base %s: virtual-size = %d MB, solicitado = %d MB",
                image_path, backing_mb, requested_mb,
            )
        except Exception as exc:
            logger.warning(
                "No se pudo leer virtual-size de %s (%s), usando tamaño solicitado",
                image_path, exc,
            )
            backing_mb = 0

        # El overlay NUNCA debe ser menor que la imagen base
        final_mb = max(requested_mb, backing_mb)
        size_arg = f"{final_mb}M"

        self._exec_checked(
            f"sudo qemu-img create -f qcow2 -b {image_path} -F qcow2 {disk_path} {size_arg}"
        )
        logger.info("Disco creado: %s usando imagen %s con tamaño %s", disk_path, image_path, size_arg)
        return disk_path



    def _prepare_cloud_init(self, vm_id: str, image_path: str, vm_user: str = "ubuntu", vm_password: str = "pucp2026", public_key_path: str = "keys/worker_key.pub", owner_ssh_key: str = "", image_default_username: str = "", tap_interfaces=None) -> str:
        """Genera el ISO de cloud-init en el worker fisico para inyectar la llave SSH y credenciales."""

        try:
            with open(public_key_path, "r") as f:
                pub_key = f.read().strip()
        except Exception:
            pub_key = ""

        # Usuario real por defecto de la imagen: prioriza el valor registrado
        # al subir la imagen (Image.default_username) sobre el heurístico por
        # ruta de archivo — el heurístico solo distingue "cirros" y asume
        # "ubuntu" para todo lo demás, lo que rompe imágenes Debian (usuario
        # real "debian") u otras distros con un default_username distinto.
        default_image_user = image_default_username or ("cirros" if "cirros" in image_path.lower() else "ubuntu")

        # Construimos las entradas de usuarios. El usuario por defecto se
        # declara explícito (no con el sentinela `default`) porque es la
        # única forma de forzar `lock_passwd: false` sobre él — sin esto,
        # cloud-init hereda el `lock_passwd: true` que trae el datasource de
        # la imagen y el login por password sigue rechazado aunque chpasswd
        # ya le haya seteado el hash.
        users_block = f"""users:
  - name: {default_image_user}
    lock_passwd: false
    sudo: ['ALL=(ALL) NOPASSWD:ALL']
    groups: sudo
    shell: /bin/bash
"""
        # Si el usuario custom es diferente al de la imagen base, agregarlo como usuario adicional
        if vm_user != default_image_user:
            users_block += f"""  - name: {vm_user}
    lock_passwd: false
    ssh-authorized-keys:
      - {pub_key}
"""
            if owner_ssh_key:
                users_block += f"      - {owner_ssh_key}\n"
            users_block += """    sudo: ['ALL=(ALL) NOPASSWD:ALL']
    groups: sudo
    shell: /bin/bash
"""

        # Llave pública del dueño del slice (REQ-US-02): también para el usuario
        # por defecto de la imagen, vía ssh_authorized_keys top-level.
        owner_keys_block = ""
        if owner_ssh_key:
            owner_keys_block = f"ssh_authorized_keys:\n  - {owner_ssh_key}\n"

        # Siempre seteamos la contraseña para ambos usuarios
        chpasswd_list = f"{default_image_user}:{vm_password}"
        if vm_user != default_image_user:
            chpasswd_list += f"\n    {vm_user}:{vm_password}"

        user_data = f"""#cloud-config
ssh_pwauth: true
{users_block}{owner_keys_block}
chpasswd:
  list: |
    {chpasswd_list}
  expire: False
"""
        meta_data = f"instance-id: {vm_id}\nlocal-hostname: {vm_id}\n"

        # Usamos Python en el worker (mas confiable que heredoc via SSH)
        # Escapamos las comillas simples para evitar problemas con el shell
        user_data_escaped = user_data.replace("'", "'\\''")
        meta_data_escaped = meta_data.replace("'", "'\\''")

        # Network-config opcional: IPs manuales por interfaz de enlace.
        # Solo se genera si alguna TAP trae ip_cidr -> si no, no se escribe
        # network-config y el comportamiento es idéntico al actual (la gestión
        # toma DHCP del gateway y los enlaces quedan sin IP, solo capa 2).
        network_config = _build_network_config(tap_interfaces)
        write_netcfg = ""
        netcfg_flag = ""
        if network_config:
            network_config_escaped = network_config.replace("'", "'\\''")
            write_netcfg = f"printf '%s' '{network_config_escaped}' > /tmp/{vm_id}_network-config && "
            netcfg_flag = f"--network-config /tmp/{vm_id}_network-config "

        cmd_cloud_init = (
            f"printf '%s' '{user_data_escaped}' > /tmp/{vm_id}_user-data && "
            f"printf '%s' '{meta_data_escaped}' > /tmp/{vm_id}_meta-data && "
            f"{write_netcfg}"
            f"sudo cloud-localds {netcfg_flag}/vms/{vm_id}_seed.iso /tmp/{vm_id}_user-data /tmp/{vm_id}_meta-data"
        )

        self._ssh.exec(cmd_cloud_init)
        logger.info("cloud-init ISO generado para VM %s (usuario: %s, default: %s, net_manual: %s)", vm_id, vm_user, default_image_user, bool(network_config))
        return f"/vms/{vm_id}_seed.iso"


    def launch_vm(
        self,
        vm_id:          str,
        slice_id:       str,
        disk_path:      str,
        vcpus:          int,
        ram_mb:         int,
        vnc_display:    int,
        tap_interfaces: List[TapInterface],
        image_path:     str,
        vm_user:        str = "ubuntu",
        vm_password:    str = "pucp2026",
        priority:       int = 0,
        owner_ssh_key:  str = "",
        image_default_username: str = "",
    ) -> int:
        """
        Lanza el proceso QEMU/KVM en el worker.
        Returns: PID del proceso QEMU.
        """
        name    = f"{vm_id}-{slice_id}"
        net_args = _build_net_args(tap_interfaces)

        linux_nice = priority - 20

        # Generamos el cloud-init ISO con usuario y contraseña configurados
        seed_iso_path = self._prepare_cloud_init(vm_id, image_path, vm_user=vm_user, vm_password=vm_password, owner_ssh_key=owner_ssh_key, image_default_username=image_default_username, tap_interfaces=tap_interfaces)
        
        cmd = (
            f"sudo nice -n {linux_nice} "
            f"qemu-system-x86_64 "
            f"-enable-kvm "
            f"-name {name} "
            f"-m {ram_mb} "
            f"-smp {vcpus} "
            f"-drive file={disk_path},format=qcow2 "
            f"-cdrom {seed_iso_path} "
            f"-vnc 0.0.0.0:{vnc_display},websocket={vnc_display + 5700} "
            f"{net_args}"
            # Socket QMP para hot-plug de NICs (Modo Edición, REQ-US-14).
            # Local al worker (/tmp, no NFS) — se conecta por SSH cuando se
            # necesita agregar un enlace a esta VM sin reiniciarla.
            f"-qmp unix:/tmp/qmp-{vm_id}-{slice_id}.sock,server,nowait "
            f"-daemonize"
        )

        self._exec_checked(cmd)
        pid = self._get_pid(name)
        logger.info("VM %s lanzada, PID=%d, VNC=:%d", name, pid, vnc_display)
        return pid

    def _get_pid(self, vm_name: str) -> int:
        out = self._exec_checked(f"pgrep -f 'name {vm_name}'")
        try:
            return int(out.splitlines()[0])
        except (ValueError, IndexError) as exc:
            raise RuntimeError(
                f"No se pudo obtener PID para '{vm_name}': {out}"
            ) from exc

    # ------------------------------------------------------------------
    # Destroy
    # ------------------------------------------------------------------

    def kill_vm(self, vm_id: str, slice_id: str) -> None:
        """Mata el proceso QEMU de la VM si está corriendo."""
        name = f"{vm_id}-{slice_id}"
        code, _, _ = self._ssh.exec(f"pgrep -f 'name {name}'")
        if code == 0:
            self._ssh.exec(f"sudo pkill -9 -f 'name {name}'")
            logger.info("VM %s terminada", name)
        else:
            logger.warning("VM %s no encontrada (ya estaba muerta)", name)

    def delete_disk(self, vm_id: str, slice_id: str) -> None:
        """Elimina el disco QCOW2 de la VM."""
        disk_path = get_vm_disk_path(vm_id, slice_id)
        code, _, _ = self._ssh.exec(f"test -f {disk_path}")
        if code == 0:
            self._exec_checked(f"sudo rm -f {disk_path}")
            logger.info("Disco eliminado: %s", disk_path)
        else:
            logger.warning("Disco no encontrado (ya eliminado): %s", disk_path)

    def hotplug_nic(self, vm_id: str, slice_id: str, tap_name: str, mac: str,
                     pci_slot: int | None = None) -> None:
        """
        Conecta una NIC en caliente a una VM QEMU en ejecución vía QMP
        (Modo Edición, REQ-US-14). El TAP ya debe existir en el worker
        (lo crea el NetworkOrchestrator en el paso de red).

        Requiere que la VM haya sido lanzada con el socket QMP
        (/tmp/qmp-{vm_id}-{slice_id}.sock). Las VMs desplegadas antes de
        esta versión no lo tienen: hay que redesplegar el slice una vez.

        `pci_slot`, si viene dado, fija el `addr=` del device — así el ensN
        real dentro del guest queda determinado por SliceManager (que lo
        calcula y persiste) en vez de dejarlo a elección de QEMU, que podría
        reusar un slot recién liberado por un unplug anterior.
        """
        qmp_path  = f"/tmp/qmp-{vm_id}-{slice_id}.sock"
        netdev_id, dev_id = net_qmp_ids(tap_name)
        addr_arg = f",'addr':{hex(pci_slot)!r}" if pci_slot is not None else ""

        # Script QMP ejecutado EN el worker (el socket es local a él).
        # Handshake → netdev_add(tap) → device_add(virtio-net-pci).
        qmp_script = (
            "import socket,json,sys\n"
            "s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)\n"
            f"s.connect({qmp_path!r})\n"
            "f=s.makefile('rw')\n"
            "f.readline()\n"
            "def cmd(c):\n"
            "    f.write(json.dumps(c)+'\\n'); f.flush()\n"
            "    while True:\n"
            "        r=json.loads(f.readline())\n"
            "        if 'return' in r or 'error' in r: return r\n"
            "cmd({'execute':'qmp_capabilities'})\n"
            f"r1=cmd({{'execute':'netdev_add','arguments':{{'type':'tap','id':{netdev_id!r},"
            f"'ifname':{tap_name!r},'script':'no','downscript':'no'}}}})\n"
            "if 'error' in r1: print('NETDEV_ERR:'+r1['error'].get('desc','?')); sys.exit(1)\n"
            f"r2=cmd({{'execute':'device_add','arguments':{{'driver':'virtio-net-pci',"
            f"'netdev':{netdev_id!r},'mac':{mac!r},'id':{dev_id!r}{addr_arg}}}}})\n"
            "if 'error' in r2: print('DEVICE_ERR:'+r2['error'].get('desc','?')); sys.exit(1)\n"
            "print('HOTPLUG_OK')\n"
        )
        script_b64 = __import__("base64").b64encode(qmp_script.encode()).decode()
        # QEMU corre con sudo → el socket QMP es de root → sudo python3
        cmd = f"echo {script_b64} | base64 -d | sudo python3 -"

        exit_code, out, err = self._ssh.exec(cmd)
        output = (out or "") + (err or "")
        if "HOTPLUG_OK" not in output:
            raise RuntimeError(
                f"Hot-plug QMP falló para VM {vm_id} (tap={tap_name}): {output.strip() or 'sin salida'}. "
                f"Si la VM fue desplegada antes de habilitar QMP, redespliega el slice."
            )
        logger.info("Hot-plug OK: VM %s ← NIC %s (MAC %s) vía QMP", vm_id, tap_name, mac)

    def unplug_nic(self, vm_id: str, slice_id: str, tap_name: str) -> None:
        """
        Desconecta en caliente la NIC asociada a `tap_name` de una VM QEMU en
        ejecución (inverso de hotplug_nic — Modo Edición eliminación, REQ-US-14):
        QMP device_del + netdev_del, y luego elimina el TAP del kernel/OVS.
        Best-effort: no lanza si la VM/tap ya no existen.
        """
        qmp_path  = f"/tmp/qmp-{vm_id}-{slice_id}.sock"
        netdev_id, dev_id = net_qmp_ids(tap_name)

        qmp_script = (
            "import socket,json,sys\n"
            "try:\n"
            "    s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)\n"
            f"    s.connect({qmp_path!r})\n"
            "except Exception as e:\n"
            "    print('NOSOCK:'+str(e)); sys.exit(0)\n"
            "f=s.makefile('rw'); f.readline()\n"
            "def cmd(c):\n"
            "    f.write(json.dumps(c)+'\\n'); f.flush()\n"
            "    while True:\n"
            "        r=json.loads(f.readline())\n"
            "        if 'return' in r or 'error' in r: return r\n"
            "cmd({'execute':'qmp_capabilities'})\n"
            f"r1=cmd({{'execute':'device_del','arguments':{{'id':{dev_id!r}}}}})\n"
            "if 'error' in r1: print('DEVICE_DEL_ERR:'+r1['error'].get('desc','?')); sys.exit(0)\n"
            "import time; time.sleep(1)\n"
            f"r2=cmd({{'execute':'netdev_del','arguments':{{'id':{netdev_id!r}}}}})\n"
            "if 'error' in r2: print('NETDEV_DEL_ERR:'+r2['error'].get('desc','?')); sys.exit(0)\n"
            "print('UNPLUG_OK')\n"
        )
        script_b64 = __import__("base64").b64encode(qmp_script.encode()).decode()
        exit_code, out, err = self._ssh.exec(f"echo {script_b64} | base64 -d | sudo python3 -")
        output = (out or "") + (err or "")
        # Limpiar el TAP del OVS y del kernel (ya sin peer). Se hace incluso si
        # el QMP falló (best-effort del lado del host), pero el resultado real
        # del device_del SÍ se loguea — antes esto reportaba "Unplug OK"
        # incondicionalmente aunque QMP hubiera fallado en silencio.
        self._ssh.exec(f"sudo ovs-vsctl --if-exists del-port br-int {tap_name}")
        self._ssh.exec(f"sudo ip link del {tap_name} 2>/dev/null || true")
        if "UNPLUG_OK" not in output:
            logger.warning(
                "Unplug QMP incompleto para VM %s (tap=%s, dev_id=%s): %s — "
                "el TAP del host se borró igual, pero la NIC pudo quedar viva dentro de la VM.",
                vm_id, tap_name, dev_id, output.strip() or "sin salida"
            )
        else:
            logger.info("Unplug OK: VM %s ✂ NIC %s", vm_id, tap_name)

    def delete_seed_iso(self, vm_id: str) -> None:
        """Elimina el ISO de cloud-init generado al arrancar la VM."""
        iso_path = f"/vms/{vm_id}_seed.iso"
        code, _, _ = self._ssh.exec(f"test -f {iso_path}")
        if code == 0:
            self._ssh.exec(f"sudo rm -f {iso_path}")
            logger.info("Seed ISO eliminado: %s", iso_path)
        else:
            logger.debug("Seed ISO no encontrado: %s", iso_path)




# ------------------------------------------------------------------
# Helpers privados
# ------------------------------------------------------------------

def net_qmp_ids(tap_name: str) -> tuple[str, str]:
    """IDs QMP determinísticos derivados del tap_name.

    Deben coincidir SIEMPRE entre el lanzamiento inicial (_build_net_args) y
    hotplug_nic/unplug_nic — si una NIC original se lanza con un id distinto
    (p.ej. posicional 'net0'/'net1'), unplug_nic no la encuentra: QMP responde
    error, el script lo ignora y de todos modos reporta éxito, así que el TAP
    del host se borra pero la interfaz sigue viva dentro de la VM.
    """
    return f"hp-{tap_name[-12:]}", f"nic-{tap_name[-12:]}"


def _build_net_args(tap_interfaces: List[TapInterface]) -> str:
    """
    Construye los argumentos -netdev/-device para QEMU.

    Ejemplo con 2 TAPs:
      -netdev tap,id=hp-tap-vm1-0,ifname=tap-vm1-0,script=no,downscript=no
      -device virtio-net-pci,netdev=hp-tap-vm1-0,mac=52:54:00:A3:C7:00,id=nic-tap-vm1-0
      -netdev tap,id=hp-tap-vm1-1,ifname=tap-vm1-1,script=no,downscript=no
      -device virtio-net-pci,netdev=hp-tap-vm1-1,mac=52:54:00:A3:C7:01,id=nic-tap-vm1-1
    """
    if not tap_interfaces:
        return "-netdev user,id=net0 -device virtio-net-pci,netdev=net0 "

    parts = []
    for iface in tap_interfaces:
        netdev_id, dev_id = net_qmp_ids(iface.tap_name)
        slot = getattr(iface, "pci_slot", None)
        addr = f",addr={hex(slot)}" if slot is not None else ""
        parts.append(
            f"-netdev tap,id={netdev_id},ifname={iface.tap_name},"
            f"script=no,downscript=no "
        )
        parts.append(
            f"-device virtio-net-pci,netdev={netdev_id},mac={iface.mac},id={dev_id}{addr} "
        )
    return "".join(parts)


def _build_network_config(tap_interfaces) -> str | None:
    """
    Genera un cloud-init network-config v2 (netplan) para las interfaces de
    enlace del guest.

    Se genera SIEMPRE que haya al menos una tap (no solo cuando alguna trae
    `ip_cidr`): todas las interfaces —también la de gestión, cuyo VLAN de mgmt
    también cruza el trunk inter-worker— reciben `mtu: DATA_TRUNK_MTU`. Sin
    esto, la VM sigue generando frames de 1500 sin que nada se lo impida, y
    como el descarte real ocurre fuera del stack IP del worker (ver comentario
    en config.py), nunca llega el ICMP que permitiría a la VM autoajustarse
    por PMTUD — el mismo cuelgue de SSH/TLS que ya se vio se puede repetir en
    cualquier VM cuyo enlace cruce ese tramo, tenga o no IP manual.

    El match es por MAC (no por nombre ensN): determinístico e independiente
    de cómo el guest nombre la interfaz.
      · IP definida  -> estática (dhcp4: false + addresses).
      · Gestión      -> dhcp4: true (la sirve el dnsmasq del gateway).
      · Enlace vacío -> dhcp4: false, sin dirección (solo capa 2, como hoy).

    Acepta objetos TapInterface o dicts, por robustez.
    """
    taps = tap_interfaces or []
    if not taps:
        return None

    def _g(t, k):
        return t.get(k) if isinstance(t, dict) else getattr(t, k, None)

    mtu = getattr(settings, "DATA_TRUNK_MTU", 0) or 0

    lines = ["version: 2", "ethernets:"]
    for idx, t in enumerate(taps):
        mac = (_g(t, "mac") or "").lower()
        if not mac:
            continue
        ip = _g(t, "ip_cidr")
        # Gestión = pci_slot 3 (convención SliceManager) o, en su defecto, la
        # primera NIC. Siempre DHCP para no perder el acceso por el gateway.
        is_mgmt = (_g(t, "pci_slot") == 3) or (idx == 0)
        lines.append(f"  nic{idx}:")
        lines.append(f"    match:")
        lines.append(f"      macaddress: \"{mac}\"")
        if mtu:
            lines.append(f"    mtu: {mtu}")
        if ip:
            lines.append(f"    dhcp4: false")
            lines.append(f"    addresses:")
            lines.append(f"      - \"{ip}\"")
        elif is_mgmt:
            lines.append(f"    dhcp4: true")
        else:
            lines.append(f"    dhcp4: false")
    return "\n".join(lines) + "\n"
