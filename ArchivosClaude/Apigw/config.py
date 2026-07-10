from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # URL interna del Slice Manager (nombre de servicio Docker)
    SLICE_MANAGER_URL: str = "http://slice-manager:8000"

    # Timeout en segundos para el reenvío de requests
    FORWARD_TIMEOUT: float = 30.0

    LOG_LEVEL: str = "INFO"

    # ──────────────────────────────────────────────────────────────
    # JWT — Validación de tokens Keycloak
    #
    # JWT_ENABLED:   activar/desactivar el middleware en main.py
    # JWT_JWKS_URL:  endpoint JWKS de Keycloak (nombre de servicio Docker)
    # JWT_ISSUER:    debe coincidir EXACTAMENTE con el campo `iss` del token
    #                → usa la IP pública de la VM (KC_HOSTNAME_URL de Keycloak)
    # JWT_AUDIENCE:  si está vacío, no se verifica el claim `aud`
    # ──────────────────────────────────────────────────────────────
    JWT_ENABLED:  bool = False
    JWT_JWKS_URL: str  = "http://keycloak:8080/realms/pucp-cloud/protocol/openid-connect/certs"
    JWT_ISSUER:   str  = "http://10.20.11.212:8086/realms/pucp-cloud"
    JWT_AUDIENCE: str  = ""     # vacío → no verificar aud (flexible para dev/prod)

    class Config:
        env_file = ".env"


settings = Settings()
