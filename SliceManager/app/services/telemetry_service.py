# app/services/telemetry_service.py
"""
Telemetría en vivo de una VM individual (REQ: "Ver Telemetría" en el NodeEditor).

Dos estrategias, según la zona — no hay una fuente común de métricas por VM:
  - OpenStack: Nova expone diagnóstico real por instancia (CPU/RAM/uptime)
    vía la API de diagnostics. Usamos la misma conexión openstacksdk que ya
    usa gc_scheduler.py para Glance.
  - Linux Cluster: las VMs son procesos qemu-system-x86_64 lanzados por SSH
    directo, SIN pasar por libvirt — el libvirt_exporter que ya está tuneleado
    en Observability solo reporta un conteo de dominios (`libvirt_domains`),
    no hay métricas por VM ahí. En su lugar, se hace SSH al worker y se le
    pide al propio `ps` que calcule %CPU/%MEM del proceso qemu (ya resuelve
    el cálculo de uso de CPU sin necesitar dos muestreos nuestros).
"""
import logging
import os

import paramiko

from app.models import Vm, Worker

logger = logging.getLogger("SliceManager.Telemetry")

OPENSTACK_AZ_ID = int(os.getenv("OPENSTACK_AZ_ID", "2"))


def _get_openstack_connection():
    import openstack
    auth_url = os.getenv("OS_AUTH_URL")
    if not auth_url:
        raise RuntimeError("OS_AUTH_URL no configurado")
    return openstack.connect(
        auth_url=auth_url,
        username=os.getenv("OS_USERNAME", "admin"),
        password=os.getenv("OS_PASSWORD", ""),
        project_name=os.getenv("OS_PROJECT_NAME", "admin"),
        user_domain_name=os.getenv("OS_USER_DOMAIN_NAME", "Default"),
        project_domain_name=os.getenv("OS_PROJECT_DOMAIN_NAME", "Default"),
    )


def _openstack_telemetry(vm: Vm) -> dict:
    if not vm.provider_instance_id:
        return {"available": False, "reason": "La VM no tiene provider_instance_id registrado."}

    try:
        conn = _get_openstack_connection()
        diag = conn.compute.get_server_diagnostics(vm.provider_instance_id)
        # El shape exacto depende de la microversion de Nova — algunos
        # despliegues devuelven un objeto tipado, otros un dict crudo.
        data = diag.to_dict() if hasattr(diag, "to_dict") else dict(diag)
    except Exception as exc:
        logger.warning("[Telemetry] Nova diagnostics falló para VM %s: %s", vm.name, exc)
        return {"available": False, "reason": f"No se pudo consultar Nova: {exc}"}

    mem = data.get("memory_details") or {}
    ram_used_mb = mem.get("used")
    ram_max_mb = mem.get("maximum")

    # cpu_details trae tiempo acumulado de CPU (ns), no un %. Sin una segunda
    # muestra no hay forma honesta de convertir esto en %CPU instantáneo —
    # se reporta el dato crudo en vez de inventar un porcentaje.
    cpu_details = data.get("cpu_details") or []
    num_cpus = data.get("num_cpus")

    return {
        "available": True,
        "source": "nova_diagnostics",
        "state": data.get("state"),
        "uptime_seconds": data.get("uptime"),
        "num_cpus": num_cpus,
        "cpu_time_ns_per_vcpu": [c.get("time") for c in cpu_details] if cpu_details else None,
        "ram_used_mb": ram_used_mb,
        "ram_max_mb": ram_max_mb,
        "ram_pct": round(100 * ram_used_mb / ram_max_mb, 1) if ram_used_mb and ram_max_mb else None,
    }


def _resolve_key_path(key_path: str) -> str | None:
    """Misma resolución que _read_ssh_key en deploy_router.py, pero devuelve
    el PATH (paramiko.connect necesita un archivo, no el contenido)."""
    if not key_path:
        return None
    for path in (key_path, f"/app/keys/{os.path.basename(key_path)}"):
        if os.path.exists(path):
            return path
    return None


def _linux_cluster_telemetry(vm: Vm, worker: Worker) -> dict:
    if not worker or not worker.ssh_key_path:
        return {"available": False, "reason": "Worker sin credenciales SSH configuradas."}

    key_file = _resolve_key_path(worker.ssh_key_path)
    if not key_file:
        return {"available": False, "reason": "No se encontró la llave SSH del worker en el contenedor."}

    match_pattern = f"{vm.name}-{vm.slice_id}"
    # pgrep encuentra el PID del qemu de ESTA VM por su -name (único: vm-slice);
    # ps calcula %CPU/%MEM del kernel en el mismo viaje SSH, sin muestreo propio.
    remote_cmd = (
        f"PID=$(pgrep -f -- '-name {match_pattern}' | head -n1); "
        f"if [ -z \"$PID\" ]; then echo NOPID; else "
        f"ps -p \"$PID\" -o %cpu,%mem,rss,etimes --no-headers; fi"
    )

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            worker.ip, port=getattr(worker, "ssh_port", 22) or 22,
            username=getattr(worker, "ssh_user", "ubuntu") or "ubuntu",
            key_filename=key_file, timeout=10,
        )
        _, stdout, stderr = client.exec_command(remote_cmd)
        out = stdout.read().decode().strip()
        err = stderr.read().decode().strip()
    except Exception as exc:
        logger.warning("[Telemetry] SSH a worker %s falló para VM %s: %s", worker.ip, vm.name, exc)
        return {"available": False, "reason": f"No se pudo conectar al worker: {exc}"}
    finally:
        client.close()

    if not out or out == "NOPID":
        return {"available": False, "reason": "El proceso QEMU de esta VM no está corriendo en el worker (¿VM apagada o recién editada?)."}

    parts = out.split()
    if len(parts) < 4:
        return {"available": False, "reason": f"Salida inesperada de 'ps' en el worker: {out!r} {err}"}

    cpu_pct, mem_pct, rss_kb, etime_s = parts[0], parts[1], parts[2], parts[3]
    return {
        "available": True,
        "source": "qemu_ps",
        "cpu_pct": float(cpu_pct),
        "ram_pct": float(mem_pct),
        "ram_used_mb": round(int(rss_kb) / 1024, 1),
        "uptime_seconds": int(etime_s),
    }


def get_vm_telemetry(vm: Vm, worker: Worker) -> dict:
    """Punto de entrada único — decide la estrategia según la zona de la VM."""
    if vm.state not in ("ACTIVE",):
        return {"available": False, "reason": f"La VM está en estado '{vm.state}', no hay proceso corriendo."}

    is_openstack = worker and getattr(worker, "availability_zones_id", None) == OPENSTACK_AZ_ID
    if is_openstack:
        return _openstack_telemetry(vm)
    return _linux_cluster_telemetry(vm, worker)
