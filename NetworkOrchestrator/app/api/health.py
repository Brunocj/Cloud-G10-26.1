"""Healthcheck HTTP para el Network Orchestrator."""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.core.config import settings
from app.services.queue_client import queue_client

logger = logging.getLogger(__name__)

class HealthHandler(BaseHTTPRequestHandler):
    """Maneja las peticiones de salud del sistema."""

    def do_GET(self):
        if self.path != "/health":
            self._respond(404, {"status": "not_found"})
            return

        # Verifica si el cliente NATS está conectado realmente
        connected = queue_client.is_connected()
        body   = {"status": "ok" if connected else "degraded", "nats": connected}
        status = 200 if connected else 503
        self._respond(status, body)

    def _respond(self, code: int, body: dict):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        # Silencia los logs de cada petición de healthcheck para no saturar la consola
        pass

def start_health_server():
    """Inicia el servidor de salud en un hilo independiente."""
    server = HTTPServer(("0.0.0.0", settings.HEALTH_PORT), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Healthcheck disponible en el puerto :{settings.HEALTH_PORT}/health")