import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.routers import (
    slices, vnc_proxy, observability, projects, users,
    requests_proxy, notifications_ws, logs,
)

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
#
# Middleware ASGI puro (no BaseHTTPMiddleware): lee el jwks_client desde
# app.state en cada request via scope["app"], sin envolver ni bufferizar
# el body — necesario para que uploads grandes (imágenes OpenStack) no se
# corrompan al pasar por el middleware.
if settings.JWT_ENABLED:
    from app.middleware.jwt_auth import JWTAuthMiddleware

    app.add_middleware(JWTAuthMiddleware)

# ── Routers ────────────────────────────────────────────────────────────────
app.include_router(slices.router)
app.include_router(vnc_proxy.router)
app.include_router(observability.router)
app.include_router(projects.router)
app.include_router(users.router)
app.include_router(requests_proxy.router)
app.include_router(notifications_ws.router)
app.include_router(logs.router)

# ── Health ─────────────────────────────────────────────────────────────────
@app.get("/health", tags=["gateway"])
async def health():
    return {
        "status":       "ok",
        "service":      "api-gateway",
        "jwt_enabled":  settings.JWT_ENABLED,
    }
