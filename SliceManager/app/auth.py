"""
app/auth.py — Dependencias de identidad para los routers del Slice Manager.

El API Gateway valida el JWT de Keycloak y reenvía la identidad del usuario
en dos headers internos:
  - X-User-Id:   sub del JWT (UUID de Keycloak)
  - X-User-Role: rol de mayor prioridad (superAdmin | admin | jefeProyecto | usuario)

Estos headers NUNCA llegan directamente del cliente — el Gateway los inyecta
después de validar el token, así que son de confianza dentro de la red Docker.

Uso en un router:
    from app.auth import CurrentUser, require_roles

    @router.get("/")
    def list_slices(user: CurrentUser = Depends(get_current_user)):
        ...

    @router.delete("/{slice_id}")
    def destroy(slice_id: int, user: CurrentUser = Depends(require_roles("admin", "superAdmin"))):
        ...
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from fastapi import Header, HTTPException

logger = logging.getLogger("SliceManager.auth")

# ── Roles válidos del sistema ─────────────────────────────────────────────────
ROLES = {"superAdmin", "admin", "jefeProyecto", "usuario"}

# Prioridad numérica (mayor = más privilegios)
ROLE_LEVEL = {
    "usuario":       1,
    "jefeProyecto":  2,
    "admin":         3,
    "superAdmin":    4,
}


@dataclass
class CurrentUser:
    """Identidad extraída de los headers X-User-Id / X-User-Role."""
    user_id: str
    role:    str

    @property
    def level(self) -> int:
        return ROLE_LEVEL.get(self.role, 0)

    def is_owner_or_above(self, owner_id: str, min_role: str = "admin") -> bool:
        """Devuelve True si es el dueño del recurso O si tiene rol >= min_role."""
        return self.user_id == owner_id or self.level >= ROLE_LEVEL.get(min_role, 99)

    def can_manage_all(self) -> bool:
        """admin y superAdmin pueden ver/gestionar slices de todos."""
        return self.level >= ROLE_LEVEL["admin"]


# ── Dependencia principal ─────────────────────────────────────────────────────

def get_current_user(
    x_user_id:   str = Header(..., alias="x-user-id",   description="Sub del JWT (inyectado por el API Gateway)"),
    x_user_role: str = Header(..., alias="x-user-role", description="Rol del usuario (inyectado por el API Gateway)"),
) -> CurrentUser:
    """
    FastAPI dependency que extrae la identidad desde los headers internos.
    Lanza 401 si los headers no están presentes (petición no pasó por el Gateway).
    Lanza 403 si el rol no es reconocido.
    """
    if x_user_role not in ROLES:
        logger.warning(
            "Rol desconocido '%s' para user_id=%s", x_user_role, x_user_id[:8]
        )
        raise HTTPException(
            status_code=403,
            detail=f"Rol '{x_user_role}' no reconocido en esta plataforma.",
        )

    logger.debug("Identidad recibida: user_id=%s… role=%s", x_user_id[:8], x_user_role)
    return CurrentUser(user_id=x_user_id, role=x_user_role)


# ── Fábrica de dependencias con rol mínimo ────────────────────────────────────

def require_roles(*allowed_roles: str) -> Callable:
    """
    Dependencia parametrizada que exige que el usuario tenga uno de los roles
    indicados. Lanza 403 si el rol no está en la lista.

    Uso:
        @router.delete("/{id}")
        def destroy(user = Depends(require_roles("admin", "superAdmin"))):
            ...
    """
    allowed = set(allowed_roles)

    def _checker(user: CurrentUser = __import__('fastapi').Depends(get_current_user)) -> CurrentUser:
        if user.role not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Acción no permitida para el rol '{user.role}'. "
                       f"Se requiere: {sorted(allowed)}.",
            )
        return user

    return _checker
