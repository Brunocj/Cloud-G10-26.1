"""
Cliente SSH para ejecutar comandos de red en workers remotos.
"""

import io
import logging
from typing import Tuple
import paramiko
from app.core.config import settings

logger = logging.getLogger(__name__)

class SSHClient:
    def __init__(self, host: str, ssh_user: str, ssh_private_key: str):
        self.host            = host
        self.user            = ssh_user
        self.ssh_private_key = ssh_private_key
        self.timeout         = settings.SSH_TIMEOUT
        self._client: paramiko.SSHClient | None = None

    def connect(self) -> None:
        pkey = paramiko.RSAKey.from_private_key(io.StringIO(self.ssh_private_key))
        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._client.connect(
            hostname=self.host,
            username=self.user,
            pkey=pkey,
            timeout=self.timeout,
        )
        logger.debug(f"SSH conectado a {self.host} como {self.user}")

    def disconnect(self) -> None:
        if self._client:
            self._client.close()
            self._client = None

    def exec(self, command: str) -> Tuple[int, str, str]:
        """Ejecuta un comando crudo de OVS o iptables en el worker."""
        if not self._client:
            raise RuntimeError(f"No hay conexión SSH activa a {self.host}")

        logger.debug(f"[{self.host}] $ {command}")
        _, stdout, stderr = self._client.exec_command(command, timeout=self.timeout)

        exit_code = stdout.channel.recv_exit_status()
        out = stdout.read().decode("utf-8", errors="replace").strip()
        err = stderr.read().decode("utf-8", errors="replace").strip()

        if exit_code != 0:
            logger.warning(f"[{self.host}] exit={exit_code} stderr={err}")
            
        return exit_code, out, err

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.disconnect()