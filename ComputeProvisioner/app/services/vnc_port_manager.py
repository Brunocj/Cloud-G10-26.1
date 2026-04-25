"""
Gestor centralizado de puertos VNC por worker.

Garantiza que nunca se asigne un puerto VNC ya en uso en un worker.
El rango válido es 5901-5999 (displays 1-99).

Estrategia:
1. Al asignar un puerto, consulta qué puertos están ocupados en el worker
   buscando procesos QEMU activos via SSH.
2. Adicionalmente mantiene un registro en memoria para coordinar asignaciones
   concurrentes dentro de la misma operación de deploy (antes de que los
   procesos QEMU estén corriendo).
3. El registro en memoria se limpia cuando las VMs son destruidas.
"""

import logging
import threading
from typing import Set

from app.services.ssh_client import SSHClient

logger = logging.getLogger(__name__)

VNC_PORT_MIN = 5901
VNC_PORT_MAX = 5999


class VNCPortManager:
    """
    Singleton thread-safe para gestión de puertos VNC.
    Combina consulta real al worker + registro en memoria para evitar
    colisiones durante despliegues concurrentes.
    """

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._reserved: dict[str, Set[int]] = {}
                    cls._instance._reserve_lock = threading.Lock()
        return cls._instance

    def assign_port(self, worker_ip: str, ssh_user: str, ssh_private_key: str) -> int:
        """
        Asigna el siguiente puerto VNC disponible en el worker.
        Nunca retorna un puerto ya en uso (ni por proceso activo ni por reserva en curso).

        Returns:
            Puerto VNC asignado (ej: 5901).

        Raises:
            RuntimeError: si no hay puertos disponibles en el rango.
        """
        with self._reserve_lock:
            in_use = self._get_ports_in_use(worker_ip, ssh_user, ssh_private_key)
            reserved = self._reserved.get(worker_ip, set())
            occupied = in_use | reserved

            for port in range(VNC_PORT_MIN, VNC_PORT_MAX + 1):
                if port not in occupied:
                    # Reservar inmediatamente para bloquear asignaciones concurrentes
                    if worker_ip not in self._reserved:
                        self._reserved[worker_ip] = set()
                    self._reserved[worker_ip].add(port)
                    logger.debug(f"[{worker_ip}] Puerto VNC {port} asignado")
                    return port

            raise RuntimeError(
                f"No hay puertos VNC disponibles en {worker_ip} "
                f"(rango {VNC_PORT_MIN}-{VNC_PORT_MAX} agotado)"
            )

    def release_port(self, worker_ip: str, port: int) -> None:
        """
        Libera un puerto VNC del registro en memoria.
        Llamar al destruir una VM.
        """
        with self._reserve_lock:
            if worker_ip in self._reserved:
                self._reserved[worker_ip].discard(port)
                logger.debug(f"[{worker_ip}] Puerto VNC {port} liberado")

    def _get_ports_in_use(self, worker_ip: str,
                          ssh_user: str, ssh_private_key: str) -> Set[int]:
        """
        Consulta al worker qué puertos VNC están actualmente ocupados
        buscando procesos QEMU con -vnc en sus argumentos.
        """
        try:
            with SSHClient(worker_ip, ssh_user, ssh_private_key) as ssh:
                # Buscar todos los argumentos -vnc de procesos QEMU activos
                exit_code, out, _ = ssh.exec(
                    "pgrep -a qemu-system-x86_64 | grep -o '\\-vnc 0\\.0\\.0\\.0:[0-9]*' | grep -o '[0-9]*$'"
                )
                if exit_code != 0 or not out:
                    return set()

                ports = set()
                for line in out.strip().splitlines():
                    try:
                        display = int(line.strip())
                        ports.add(5900 + display)
                    except ValueError:
                        pass
                return ports

        except Exception as exc:
            logger.warning(
                f"[{worker_ip}] No se pudo consultar puertos VNC en uso: {exc}. "
                f"Se usará únicamente el registro en memoria."
            )
            return set()
