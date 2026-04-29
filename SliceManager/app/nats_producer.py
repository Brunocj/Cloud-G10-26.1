import json
import logging
import os
import nats
from nats.js import JetStreamContext
from nats.errors import ConnectionClosedError, TimeoutError, NoServersError

logger = logging.getLogger("SliceManager.NATS")

class NATSProducer:
    def __init__(self):
        self.nc = None
        self.js: JetStreamContext = None

    async def connect(self, nats_url: str = "nats://localhost:4222"):
        """Establece la conexión con el servidor NATS y activa JetStream"""
        # Leemos la variable del docker-compose. Si no existe, usamos localhost por defecto.
        nats_url = os.getenv("NATS_URL", "nats://localhost:4222")
        try:
            self.nc = await nats.connect(nats_url)
            self.js = self.nc.jetstream()
            logger.info(f"✅ Conectado exitosamente a NATS en {nats_url}")
        except NoServersError:
            logger.warning(f"⚠️ No se encontró NATS en {nats_url}. Se intentará reconectar luego.")
        except Exception as e:
            logger.error(f"❌ Error conectando a NATS: {str(e)}")

    async def disconnect(self):
        """Cierra la conexión de forma limpia"""
        if self.nc and self.nc.is_connected:
            await self.nc.drain()
            logger.info("🔌 Desconectado de NATS de forma segura")

    async def publish_deploy(self, payload: dict):
        """
        Publica el mensaje en el canal 'slice.deploy' que el Queue Manager está escuchando.
        """
        if not self.js:
            logger.error("JetStream no está inicializado. No se pudo enviar el mensaje.")
            return False
            
        try:
            subject = "slice.deploy"
            data = json.dumps(payload).encode()
            
            # js.publish asegura que el mensaje se guarde en el stream
            ack = await self.js.publish(subject, data)
            logger.info(f"🚀 Orden de despliegue publicada en '{subject}'. Secuencia NATS: {ack.seq}")
            return True
        except Exception as e:
            logger.error(f"❌ Error publicando en NATS: {str(e)}")
            return False

# Instancia global para usarla en todo el proyecto
nats_producer = NATSProducer()