"""
Healthcheck HTTP mínimo para que Docker/Kubernetes pueda verificar
que el servicio está activo y conectado a Redis.
"""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.core.config import settings
from app.services.queue_client import QueueClient

logger = logging.getLogger(__name__)


class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path != "/health":
            self._respond(404, {"status": "not_found"})
            return

        queue = QueueClient()
        redis_ok = queue.ping()

        body   = {"status": "ok" if redis_ok else "degraded", "redis": redis_ok}
        status = 200 if redis_ok else 503
        self._respond(status, body)

    def _respond(self, code: int, body: dict):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass  # silenciar logs de acceso HTTP


def start_health_server():
    """Arranca el servidor de healthcheck en un hilo daemon."""
    server = HTTPServer(("0.0.0.0", settings.HEALTH_PORT), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Healthcheck disponible en :{settings.HEALTH_PORT}/health")
