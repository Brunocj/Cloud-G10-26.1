"""
app/routers/user_router.py

Gestión de usuarios: cache local + integración con Keycloak Admin API.

Endpoints:
  - POST   /api/v1/users/sync          → upsert del usuario que hace la petición (llamado al login)
  - GET    /api/v1/users/search        → buscar usuarios por nombre/email/username
  - POST   /api/v1/users/              → CREATE user in Keycloak + local cache (admin/superAdmin)
  - PATCH  /api/v1/users/{id}/role     → cambiar rol Keycloak de un usuario (superAdmin)
  - GET    /api/v1/users/              → listar todos los usuarios (admin/superAdmin)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import List, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, require_roles
from app.database import get_db
from app.models import Role, User

logger = logging.getLogger("SliceManager.users")

router = APIRouter(prefix="/api/v1/users", tags=["Users"])

# ── Keycloak Admin API config ─────────────────────────────────────────────────
KEYCLOAK_URL          = os.getenv("KEYCLOAK_URL", "http://keycloak:8080").rstrip("/")
KEYCLOAK_REALM        = os.getenv("KEYCLOAK_REALM", "pucp-cloud")
KEYCLOAK_ADMIN_USER   = os.getenv("KEYCLOAK_ADMIN_USER", "admin")
KEYCLOAK_ADMIN_PASS   = os.getenv("KEYCLOAK_ADMIN_PASS", "admin")
KEYCLOAK_ADMIN_CLIENT = os.getenv("KEYCLOAK_ADMIN_CLIENT", "admin-cli")

VALID_KC_ROLES = {"usuario", "jefeProyecto", "admin", "superAdmin"}


# ── Schemas ────────────────────────────────────────────────────────────────────

class UserSyncRequest(BaseModel):
    username: Optional[str] = None
    email:    Optional[str] = None
    fullname: Optional[str] = None
    lastname: Optional[str] = None


class UserResponse(BaseModel):
    id: str
    username: Optional[str] = None
    email:    Optional[str] = None
    fullname: Optional[str] = None
    lastname: Optional[str] = None
    role:     Optional[str] = None      # Rol global de Keycloak
    state:    Optional[str] = None

    class Config:
        from_attributes = True


class UserCreateRequest(BaseModel):
    username: str
    email:    EmailStr
    password: str
    fullname: Optional[str] = None
    lastname: Optional[str] = None
    role:     str  # "usuario" | "jefeProyecto" | "admin" | "superAdmin"


class UserRoleChangeRequest(BaseModel):
    role: str  # nuevo rol global


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_or_create_role(db: Session, role_name: str) -> Role:
    r = db.query(Role).filter(Role.role_name == role_name).first()
    if not r:
        r = Role(role_name=role_name)
        db.add(r)
        db.flush()
    return r


def _user_to_response(u: User, db: Session) -> dict:
    role = None
    if u.role_id:
        r = db.query(Role).filter(Role.id == u.role_id).first()
        role = r.role_name if r else None
    return {
        "id":       u.id,
        "username": u.username,
        "email":    u.email,
        "fullname": u.fullname,
        "lastname": u.lastname,
        "role":     role,
        "state":    u.state,
    }


async def _kc_admin_token() -> str:
    """Obtiene un access token de admin de Keycloak (realm master)."""
    url = f"{KEYCLOAK_URL}/realms/master/protocol/openid-connect/token"
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(url, data={
            "grant_type": "password",
            "client_id":  KEYCLOAK_ADMIN_CLIENT,
            "username":   KEYCLOAK_ADMIN_USER,
            "password":   KEYCLOAK_ADMIN_PASS,
        })
    if resp.status_code != 200:
        logger.error("Keycloak admin auth falló: %s %s", resp.status_code, resp.text[:200])
        raise HTTPException(status_code=502, detail="No se pudo autenticar con Keycloak Admin")
    return resp.json()["access_token"]


async def _kc_create_user(payload: UserCreateRequest) -> str:
    """Crea usuario en Keycloak, le asigna el rol y devuelve su UUID."""
    token   = await _kc_admin_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    base    = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        # 1. Crear el usuario
        create_body = {
            "username":  payload.username,
            "email":     payload.email,
            "firstName": payload.fullname or "",
            "lastName":  payload.lastname or "",
            "enabled":   True,
            "emailVerified": True,
            "credentials": [{
                "type": "password",
                "value": payload.password,
                "temporary": False,
            }],
        }
        r = await client.post(f"{base}/users", headers=headers, json=create_body)
        if r.status_code == 409:
            raise HTTPException(status_code=409, detail="Usuario ya existe en Keycloak")
        if r.status_code not in (201, 204):
            logger.error("KC create user failed: %s %s", r.status_code, r.text[:300])
            raise HTTPException(status_code=502, detail=f"Error creando usuario en Keycloak: {r.text[:150]}")

        # 2. Obtener el UUID buscando por username
        r = await client.get(f"{base}/users?username={payload.username}&exact=true", headers=headers)
        r.raise_for_status()
        users_list = r.json()
        if not users_list:
            raise HTTPException(status_code=502, detail="Usuario creado pero no encontrado en Keycloak")
        user_uuid = users_list[0]["id"]

        # 3. Asignar el rol de realm
        r = await client.get(f"{base}/roles/{payload.role}", headers=headers)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Rol '{payload.role}' no existe en Keycloak")
        role_repr = r.json()

        r = await client.post(
            f"{base}/users/{user_uuid}/role-mappings/realm",
            headers=headers, json=[role_repr],
        )
        if r.status_code not in (200, 204):
            logger.warning("KC assign role failed: %s %s", r.status_code, r.text[:200])

    return user_uuid


async def _kc_get_user_role(user_id: str) -> Optional[str]:
    """Consulta el rol Keycloak actual de un usuario (el de mayor prioridad conocido)."""
    try:
        token   = await _kc_admin_token()
        headers = {"Authorization": f"Bearer {token}"}
        base    = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            r = await client.get(f"{base}/users/{user_id}/role-mappings/realm", headers=headers)
            if r.status_code != 200:
                return None
            roles = r.json()
            # Devolver el de mayor prioridad
            priority = {"superAdmin": 4, "admin": 3, "jefeProyecto": 2, "usuario": 1}
            known = [rr["name"] for rr in roles if rr["name"] in VALID_KC_ROLES]
            if not known:
                return None
            return max(known, key=lambda n: priority.get(n, 0))
    except Exception as e:
        logger.warning("KC role lookup failed for %s: %s", user_id[:8], e)
        return None


async def _kc_change_role(user_id: str, new_role: str) -> None:
    """Reemplaza los roles Keycloak conocidos del usuario por new_role."""
    token   = await _kc_admin_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    base    = f"{KEYCLOAK_URL}/admin/realms/{KEYCLOAK_REALM}"

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Roles actuales
        r = await client.get(f"{base}/users/{user_id}/role-mappings/realm", headers=headers)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail="No se pudo leer roles de Keycloak")
        current_roles = r.json()
        # Roles a quitar (los que forman parte del set conocido)
        to_remove = [rr for rr in current_roles if rr["name"] in VALID_KC_ROLES]
        if to_remove:
            r = await client.request(
                "DELETE",
                f"{base}/users/{user_id}/role-mappings/realm",
                headers=headers, json=to_remove,
            )
            if r.status_code not in (200, 204):
                logger.warning("KC remove roles failed: %s %s", r.status_code, r.text[:200])

        # Obtener representación del nuevo rol
        r = await client.get(f"{base}/roles/{new_role}", headers=headers)
        if r.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Rol '{new_role}' no existe en Keycloak")
        role_repr = r.json()

        # Asignar el nuevo
        r = await client.post(
            f"{base}/users/{user_id}/role-mappings/realm",
            headers=headers, json=[role_repr],
        )
        if r.status_code not in (200, 204):
            raise HTTPException(status_code=502, detail=f"No se pudo asignar rol: {r.text[:150]}")


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/sync", response_model=UserResponse)
def sync_current_user(
    payload: UserSyncRequest,
    db:      Session     = Depends(get_db),
    user:    CurrentUser = Depends(get_current_user),
):
    """Upsert del usuario autenticado en la tabla local, incluyendo su rol Keycloak."""
    role = _get_or_create_role(db, user.role)

    u = db.query(User).filter(User.id == user.user_id).first()
    if u:
        if payload.username: u.username = payload.username
        if payload.email:    u.email    = payload.email
        if payload.fullname: u.fullname = payload.fullname
        if payload.lastname: u.lastname = payload.lastname
        u.role_id = role.id  # mantener rol sincronizado
    else:
        u = User(
            id=user.user_id,
            username=payload.username,
            email=payload.email,
            fullname=payload.fullname,
            lastname=payload.lastname,
            state="Activo",
            date_creation=datetime.utcnow().isoformat(timespec="seconds"),
            role_id=role.id,
        )
        db.add(u)
    db.commit()
    db.refresh(u)
    logger.info("Usuario sincronizado: id=%s… email=%s role=%s", user.user_id[:8], u.email, user.role)
    return _user_to_response(u, db)


@router.get("/search", response_model=List[UserResponse])
def search_users(
    q:     str          = "",
    limit: int          = 20,
    db:    Session      = Depends(get_db),
    user:  CurrentUser  = Depends(get_current_user),
):
    """Busca usuarios en el cache local, devolviendo su rol Keycloak."""
    query = db.query(User)
    if q.strip():
        like = f"%{q.strip()}%"
        query = query.filter(
            or_(
                User.username.ilike(like),
                User.email.ilike(like),
                User.fullname.ilike(like),
                User.lastname.ilike(like),
            )
        )
    users = query.limit(min(max(limit, 1), 100)).all()
    return [_user_to_response(u, db) for u in users]


@router.get("/", response_model=List[UserResponse])
async def list_all_users(
    db:   Session     = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin", "superAdmin")),
):
    """
    Lista todos los usuarios. Si algún registro no tiene role_id (usuarios
    legacy o creados manualmente), lo resuelve desde Keycloak y lo persiste.
    """
    users = db.query(User).order_by(User.date_creation.desc()).all()

    # Auto-heal: para users sin role_id, consultar Keycloak y persistir
    dirty = False
    for u in users:
        if not u.role_id:
            kc_role = await _kc_get_user_role(u.id)
            if kc_role:
                role = _get_or_create_role(db, kc_role)
                u.role_id = role.id
                dirty = True
    if dirty:
        db.commit()

    return [_user_to_response(u, db) for u in users]


@router.post("/", response_model=UserResponse, status_code=201)
async def create_user(
    payload: UserCreateRequest,
    db:      Session     = Depends(get_db),
    user:    CurrentUser = Depends(require_roles("admin", "superAdmin")),
):
    """
    Crea un usuario en Keycloak Y en el cache local en una sola operación.
    - admin: puede crear usuario y jefeProyecto
    - superAdmin: puede crear cualquiera incluyendo admin y superAdmin
    """
    if payload.role not in VALID_KC_ROLES:
        raise HTTPException(status_code=400, detail=f"Rol inválido. Válidos: {sorted(VALID_KC_ROLES)}")

    # Admin no puede crear otros admin/superAdmin
    if user.role == "admin" and payload.role in ("admin", "superAdmin"):
        raise HTTPException(status_code=403, detail="Solo superAdmin puede crear usuarios admin/superAdmin")

    # 1. Crear en Keycloak
    user_uuid = await _kc_create_user(payload)

    # 2. Cache local
    role = _get_or_create_role(db, payload.role)
    u = User(
        id=user_uuid,
        username=payload.username,
        email=payload.email,
        fullname=payload.fullname,
        lastname=payload.lastname,
        state="Activo",
        date_creation=datetime.utcnow().isoformat(timespec="seconds"),
        role_id=role.id,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    logger.info("Usuario creado en KC+local: %s (%s) por %s…", payload.username, payload.role, user.user_id[:8])
    return _user_to_response(u, db)


@router.patch("/{user_id}/role", response_model=UserResponse)
async def change_user_role(
    user_id: str,
    payload: UserRoleChangeRequest,
    db:      Session     = Depends(get_db),
    user:    CurrentUser = Depends(require_roles("superAdmin")),
):
    """Cambia el rol Keycloak de un usuario. Solo superAdmin."""
    if payload.role not in VALID_KC_ROLES:
        raise HTTPException(status_code=400, detail=f"Rol inválido. Válidos: {sorted(VALID_KC_ROLES)}")

    u = db.query(User).filter(User.id == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="Usuario no encontrado en cache local")

    # 1. Cambiar en Keycloak
    await _kc_change_role(user_id, payload.role)

    # 2. Actualizar cache local
    role = _get_or_create_role(db, payload.role)
    u.role_id = role.id
    db.commit()
    db.refresh(u)
    logger.info("Rol cambiado: user=%s… nuevo=%s por %s…", user_id[:8], payload.role, user.user_id[:8])
    return _user_to_response(u, db)
