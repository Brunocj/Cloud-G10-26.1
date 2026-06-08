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
    def __init__(self, host: str, ssh_user: str, ssh_private_key: str, port: int = 22):
        self.host            = host
        self.port            = port
        self.user            = ssh_user
        self.ssh_private_key = ssh_private_key
        self.timeout         = settings.SSH_TIMEOUT
        self._client: paramiko.SSHClient | None = None

    def connect(self) -> None:
        # Intentar cargar la clave PEM desde string en memoria — soporta RSA y Ed25519
        pkey = None
        key_content = self.ssh_private_key.strip() if self.ssh_private_key else ""

        if not key_content:
            raise ValueError(f"ssh_private_key vacío para {self.host}:{self.port}")

        key_stream = io.StringIO(key_content)
        last_exc: Exception | None = None
        for key_class in (paramiko.Ed25519Key, paramiko.RSAKey, paramiko.ECDSAKey):
            try:
                key_stream.seek(0)
                pkey = key_class.from_private_key(key_stream)
                break
            except Exception as exc:
                last_exc = exc

        if pkey is None:
            raise ValueError(
                f"No se pudo cargar la clave SSH para {self.host}:{self.port}: {last_exc}"
            )

        self._client = paramiko.SSHClient()
        self._client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self._client.connect(
            hostname=self.host,
            port=self.port,
            username=self.user,
            pkey=pkey,
            timeout=self.timeout,
        )
        logger.debug(f"SSH conectado a {self.host}:{self.port} como {self.user}")

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