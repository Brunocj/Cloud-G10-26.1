import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import slices, vnc_proxy

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("api-gateway")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Cliente HTTP compartido (connection pool) ──────────────────
    app.state.http_client = httpx.AsyncClient(timeout=settings.FORWARD_TIMEOUT, trust_env=False)
    logger.info("API Gateway iniciado. Slice Manager: %s", settings.SLICE_MANAGER_URL)

    # ── JWKS client: descarga claves públicas de Keycloak al arrancar ──
    if settings.JWT_ENABLED:
        from app.middleware.jwt_auth import build_jwks_client
        try:
            app.state.jwks_client = build_jwks_client()
            logger.info(
                "JWT habilitado. JWKS: %s | Issuer: %s",
                settings.JWT_JWKS_URL,
                settings.JWT_ISSUER,
            )
        except Exception as exc:
            logger.error(
                "No se pudo cargar el JWKS de Keycloak: %s. "
                "El gateway arrancará SIN validación JWT.",
                exc,
            )
            app.state.jwks_client = None
    else:
        logger.warning(
            "JWT_ENABLED=false — el gateway acepta requests SIN autenticación."
        )

    yield

    await app.state.http_client.aclose()
    logger.info("API Gateway detenido.")


# ── Aplicación ─────────────────────────────────────────────────────────────
app = FastAPI(
    title="PUCP Cloud Orchestrator — API Gateway",
    version="1.0.0",
    lifespan=lifespan,
)

# ── CORS ───────────────────────────────────────────────────────────────────
# IMPORTANTE: CORSMiddleware debe registrarse ANTES que JWTAuthMiddleware
# para que los preflight OPTIONS pasen sin necesitar token.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",           # Vite dev (local)
        "http://10.20.11.212:5173",        # Vite dev (VM)
        "http://10.20.11.212:8085",        # Gateway expuesto
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-User-Id", "X-User-Role"],
)

# ── JWT Middleware ─────────────────────────────────────────────────────────
# Se registra DESPUÉS de CORS para que OPTIONS ya haya sido manejado.
if settings.JWT_ENABLED:
    from app.middleware.jwt_auth import JWTAuthMiddleware

    @app.middleware("http")
    async def jwt_middleware(request, call_next):
        """
        Wrapper que inyecta el JWTAuthMiddleware usando el jwks_client
        inicializado en el lifespan (disponible en app.state).
        """
        from starlette.responses import JSONResponse
        jwks = request.app.state.jwks_client
        if jwks is None:
            # Keycloak no disponible al arrancar — rechazar todas las peticiones
            return JSONResponse(
                {"detail": "Servicio de autenticación no disponible"},
                status_code=503,
            )
        mw = JWTAuthMiddleware(app=None, jwks_client=jwks)
        return await mw.dispatch(request, call_next)

# ── Routers ────────────────────────────────────────────────────────────────
app.include_router(slices.router)
app.include_router(vnc_proxy.router)


# ── Health ─────────────────────────────────────────────────────────────────
@app.get("/health", tags=["gateway"])
async def health():
    return {
        "status":       "ok",
        "service":      "api-gateway",
        "jwt_enabled":  settings.JWT_ENABLED,
    }
