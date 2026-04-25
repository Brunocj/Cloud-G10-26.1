"""Healthcheck HTTP mínimo."""

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from app.core.config import settings
from app.services.nats_client import nats_manager

logger = logging.getLogger(__name__)


class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path != "/health":
            self._respond(404, {"status": "not_found"})
            return

        connected = nats_manager.is_connected()
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
        pass


def start_health_server():
    server = HTTPServer(("0.0.0.0", settings.HEALTH_PORT), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Healthcheck disponible en :{settings.HEALTH_PORT}/health")
