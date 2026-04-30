from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # URL interna del Slice Manager (nombre de servicio Docker)
    SLICE_MANAGER_URL: str = "http://slice-manager:8000"

    # Timeout en segundos para el reenvío de requests
    FORWARD_TIMEOUT: float = 30.0

    LOG_LEVEL: str = "INFO"

    # ──────────────────────────────────────────────────────────────
    # JWT — no usado todavía, pero declarado para que la config
    # ya esté disponible cuando se implemente el middleware.
    #
    # Para activar la validación JWT:
    #   1. Poner JWT_ENABLED=true en el docker-compose / .env
    #   2. Implementar app/middleware/jwt_auth.py (ver comentarios ahí)
    #   3. Registrar el middleware en app/main.py
    # ──────────────────────────────────────────────────────────────
    JWT_ENABLED: bool = False
    JWT_JWKS_URL: str = ""        # ej: http://keycloak:8080/auth/realms/pucp-cloud/protocol/openid-connect/certs
    JWT_AUDIENCE: str = ""        # ej: account
    JWT_ISSUER: str = ""          # ej: http://keycloak:8080/auth/realms/pucp-cloud

    class Config:
        env_file = ".env"


settings = Settings()
