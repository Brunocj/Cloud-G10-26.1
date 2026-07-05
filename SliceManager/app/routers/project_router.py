"""
app/routers/project_router.py

CRUD de proyectos y gestión de miembros (user_projects).

Autorización:
  - admin / superAdmin       → CRUD completo de proyectos y miembros
  - jefeProyecto             → ve sus proyectos (donde figura como miembro con rol jefe)
                                y puede gestionar miembros de esos proyectos
  - usuario                  → solo ve los proyectos donde es miembro
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.auth import CurrentUser, get_current_user, require_roles
from app.database import get_db
from app.models import Project, Role, User, UserProject

logger = logging.getLogger("SliceManager.projects")

router = APIRouter(prefix="/api/v1/projects", tags=["Projects"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class ProjectResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    date_creation: Optional[str] = None
    member_count: int = 0
    leader_count: int = 0

    class Config:
        from_attributes = True


class MemberAdd(BaseModel):
    user_id: str          # UUID de Keycloak
    project_role_name: str  # "usuario" | "jefeProyecto"


class MemberResponse(BaseModel):
    user_id: str
    username: Optional[str] = None
    fullname: Optional[str] = None
    email: Optional[str] = None
    project_role: str
    global_role: Optional[str] = None
    date_join: Optional[str] = None

    class Config:
        from_attributes = True


class ProjectDetailResponse(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    date_creation: Optional[str] = None
    members: List[MemberResponse] = []


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_or_create_role(db: Session, role_name: str) -> Role:
    """Obtiene el rol por nombre. Si no existe lo crea (seed automático)."""
    role = db.query(Role).filter(Role.role_name == role_name).first()
    if not role:
        role = Role(role_name=role_name)
        db.add(role)
        db.flush()
    return role


def _is_leader_of(db: Session, user_id: str, project_id: int) -> bool:
    """True si el user_id es jefeProyecto en el proyecto dado."""
    jefe_role = db.query(Role).filter(Role.role_name == "jefeProyecto").first()
    if not jefe_role:
        return False
    membership = db.query(UserProject).filter(
        UserProject.user_id == user_id,
        UserProject.project_id == project_id,
        UserProject.project_role_id == jefe_role.id,
    ).first()
    return membership is not None


def _serialize_project(db: Session, p: Project) -> dict:
    """Serializa un Project con conteos de miembros y jefes."""
    jefe_role = db.query(Role).filter(Role.role_name == "jefeProyecto").first()
    memberships = db.query(UserProject).filter(UserProject.project_id == p.id).all()
    leader_count = sum(
        1 for m in memberships if jefe_role and m.project_role_id == jefe_role.id
    )
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "date_creation": p.date_creation,
        "member_count": len(memberships),
        "leader_count": leader_count,
    }


def _serialize_member(db: Session, m: UserProject) -> dict:
    """Serializa un UserProject con datos del usuario y del rol contextual."""
    role = db.query(Role).filter(Role.id == m.project_role_id).first()
    u = db.query(User).filter(User.id == m.user_id).first()

    # Fullname: solo si hay algo real
    fullname = None
    if u:
        parts = [p for p in (u.fullname, u.lastname) if p and p.strip() and p.lower() != "none"]
        fullname = " ".join(parts) if parts else None

    # Global role del cache local
    global_role = None
    if u and u.role_id:
        gr = db.query(Role).filter(Role.id == u.role_id).first()
        global_role = gr.role_name if gr else None

    return {
        "user_id":      m.user_id,
        "username":     u.username if u else None,
        "fullname":     fullname,
        "email":        u.email if u else None,
        "project_role": role.role_name if role else "usuario",
        "global_role":  global_role,
        "date_join":    m.date_join,
    }


@router.get("/eligible-for-deploy")
def eligible_for_deploy(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Devuelve los proyectos que el usuario puede elegir al desplegar un slice.
    Cada uno incluye `direct_deploy` que indica si el despliegue es directo
    o requiere aprobación.

    Reglas:
      - admin/superAdmin: todos los proyectos, direct_deploy=True
      - jefeProyecto: sus proyectos; direct_deploy=True solo si es jefe ahí,
                      False si solo es miembro. Solo ve sus proyectos (no puede
                      elegir uno ajeno).
      - usuario: solo sus proyectos, direct_deploy=False para todos.
    """
    result = []

    if user.can_manage_all():
        # admin/superAdmin: cualquier proyecto, siempre directo
        projects = db.query(Project).order_by(Project.name).all()
        for p in projects:
            result.append({
                "project_id":    p.id,
                "project_name":  p.name,
                "direct_deploy": True,
            })
        return result

    # jefeProyecto / usuario: solo proyectos donde figura
    jefe_role = db.query(Role).filter(Role.role_name == "jefeProyecto").first()
    memberships = db.query(UserProject).filter(UserProject.user_id == user.user_id).all()
    for m in memberships:
        p = db.query(Project).filter(Project.id == m.project_id).first()
        if not p:
            continue
        is_leader_here = jefe_role is not None and m.project_role_id == jefe_role.id
        result.append({
            "project_id":    p.id,
            "project_name":  p.name,
            "direct_deploy": is_leader_here,
        })

    # Ordenar: directos primero, luego alfabético
    result.sort(key=lambda x: (not x["direct_deploy"], x["project_name"]))
    return result


# ── Helper reutilizable por el endpoint de deploy ─────────────────────────────

def can_deploy_directly(db: Session, user_id: str, user_role: str, project_id: Optional[int]) -> bool:
    """
    Determina si el usuario puede desplegar directo (sin aprobación) en el
    proyecto dado (o sin proyecto).

    Reglas:
      - admin/superAdmin: siempre directo
      - jefeProyecto: directo solo si project_id es un proyecto donde es jefe
      - resto: nunca directo (siempre requiere aprobación)
    """
    if user_role in ("admin", "superAdmin"):
        return True

    if user_role == "jefeProyecto" and project_id is not None:
        jefe_role = db.query(Role).filter(Role.role_name == "jefeProyecto").first()
        if not jefe_role:
            return False
        membership = db.query(UserProject).filter(
            UserProject.user_id == user_id,
            UserProject.project_id == project_id,
            UserProject.project_role_id == jefe_role.id,
        ).first()
        return membership is not None

    return False


def user_can_choose_project(db: Session, user_id: str, user_role: str, project_id: int) -> bool:
    """
    Valida que el usuario tenga derecho a elegir ese proyecto al desplegar.
    - admin/superAdmin: cualquier proyecto
    - jefe/usuario: solo proyectos donde es miembro
    """
    if user_role in ("admin", "superAdmin"):
        return db.query(Project).filter(Project.id == project_id).first() is not None
    membership = db.query(UserProject).filter(
        UserProject.user_id == user_id,
        UserProject.project_id == project_id,
    ).first()
    return membership is not None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/", response_model=List[ProjectResponse])
def list_projects(
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Lista proyectos según el rol:
    - admin / superAdmin: todos
    - resto: solo aquellos en los que es miembro
    """
    if user.can_manage_all():
        projects = db.query(Project).order_by(Project.id.desc()).all()
    else:
        membership_ids = [
            m.project_id
            for m in db.query(UserProject).filter(UserProject.user_id == user.user_id).all()
        ]
        if not membership_ids:
            return []
        projects = (
            db.query(Project)
            .filter(Project.id.in_(membership_ids))
            .order_by(Project.id.desc())
            .all()
        )
    return [_serialize_project(db, p) for p in projects]


@router.post("/", response_model=ProjectResponse, status_code=201)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin", "superAdmin")),
):
    """Crea un proyecto (solo admin / superAdmin)."""
    p = Project(
        name=payload.name,
        description=payload.description,
        date_creation=datetime.utcnow().isoformat(timespec="seconds"),
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    logger.info("Proyecto creado: id=%s name=%s por user=%s…", p.id, p.name, user.user_id[:8])
    return _serialize_project(db, p)


@router.get("/{project_id}", response_model=ProjectDetailResponse)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Detalle del proyecto con lista de miembros."""
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    # RBAC: solo admin, superAdmin, o miembros del proyecto pueden ver el detalle
    if not user.can_manage_all():
        membership = db.query(UserProject).filter(
            UserProject.project_id == project_id,
            UserProject.user_id == user.user_id,
        ).first()
        if not membership:
            raise HTTPException(status_code=403, detail="No perteneces a este proyecto")

    memberships = db.query(UserProject).filter(UserProject.project_id == project_id).all()
    return {
        "id": p.id,
        "name": p.name,
        "description": p.description,
        "date_creation": p.date_creation,
        "members": [_serialize_member(db, m) for m in memberships],
    }


@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: int,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin", "superAdmin")),
):
    """Edita nombre/descripción de un proyecto."""
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    if payload.name is not None:
        p.name = payload.name
    if payload.description is not None:
        p.description = payload.description
    db.commit()
    db.refresh(p)
    return _serialize_project(db, p)


@router.delete("/{project_id}", status_code=200)
def delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(require_roles("admin", "superAdmin")),
):
    """Elimina un proyecto y sus memberships (cascade)."""
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    db.query(UserProject).filter(UserProject.project_id == project_id).delete()
    db.delete(p)
    db.commit()
    logger.info("Proyecto eliminado: id=%s por user=%s…", project_id, user.user_id[:8])
    return {"message": f"Proyecto '{p.name}' eliminado"}


# ── Miembros ──────────────────────────────────────────────────────────────────

@router.get("/{project_id}/members", response_model=List[MemberResponse])
def list_members(
    project_id: int,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """Lista miembros del proyecto."""
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    if not user.can_manage_all():
        membership = db.query(UserProject).filter(
            UserProject.project_id == project_id,
            UserProject.user_id == user.user_id,
        ).first()
        if not membership:
            raise HTTPException(status_code=403, detail="No perteneces a este proyecto")

    memberships = db.query(UserProject).filter(UserProject.project_id == project_id).all()
    return [_serialize_member(db, m) for m in memberships]


@router.post("/{project_id}/members", response_model=MemberResponse, status_code=201)
def add_member(
    project_id: int,
    payload: MemberAdd,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Agrega un miembro al proyecto.
    Autorizado: admin/superAdmin siempre; jefeProyecto solo en proyectos donde es jefe.
    """
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    if not user.can_manage_all():
        if not (user.role == "jefeProyecto" and _is_leader_of(db, user.user_id, project_id)):
            raise HTTPException(
                status_code=403,
                detail="Solo admin, superAdmin o jefeProyecto del proyecto pueden agregar miembros",
            )

    # Validar que el rol contextual sea uno permitido
    if payload.project_role_name not in ("usuario", "jefeProyecto"):
        raise HTTPException(
            status_code=400,
            detail="project_role_name debe ser 'usuario' o 'jefeProyecto'",
        )

    # Validar que el usuario exista en el cache local (users)
    u = db.query(User).filter(User.id == payload.user_id).first()
    if not u:
        raise HTTPException(
            status_code=404,
            detail="Usuario no encontrado en el cache local. Debe haber iniciado sesión al menos una vez.",
        )

    # Si lo quieren agregar como jefeProyecto, validar que su rol GLOBAL de Keycloak lo permita
    if payload.project_role_name == "jefeProyecto":
        user_global_role = None
        if u.role_id:
            r = db.query(Role).filter(Role.id == u.role_id).first()
            user_global_role = r.role_name if r else None
        if user_global_role not in ("jefeProyecto", "admin", "superAdmin"):
            raise HTTPException(
                status_code=403,
                detail="Solo usuarios con rol global 'jefeProyecto' o superior pueden ser asignados como jefe. "
                       "Un superAdmin debe promocionar primero al usuario.",
            )

    # ¿Ya es miembro?
    existing = db.query(UserProject).filter(
        UserProject.user_id == payload.user_id,
        UserProject.project_id == project_id,
    ).first()
    if existing:
        raise HTTPException(status_code=409, detail="El usuario ya es miembro del proyecto")

    role = _get_or_create_role(db, payload.project_role_name)

    m = UserProject(
        user_id=payload.user_id,
        project_id=project_id,
        project_role_id=role.id,
        date_join=datetime.utcnow().isoformat(timespec="seconds"),
    )
    db.add(m)
    db.commit()
    db.refresh(m)
    logger.info(
        "Miembro agregado: user=%s… proyecto=%s rol=%s por=%s…",
        payload.user_id[:8], project_id, payload.project_role_name, user.user_id[:8],
    )
    return _serialize_member(db, m)


@router.delete("/{project_id}/members/{member_user_id}", status_code=200)
def remove_member(
    project_id: int,
    member_user_id: str,
    db: Session = Depends(get_db),
    user: CurrentUser = Depends(get_current_user),
):
    """
    Quita un miembro del proyecto.
    Reglas:
      - admin/superAdmin: pueden quitar a cualquier miembro (incluidos jefes)
      - jefeProyecto (del proyecto): puede quitar solo miembros con rol contextual 'usuario'.
        No puede quitar a otros jefes ni a sí mismo.
      - otros roles: no autorizados
    """
    p = db.query(Project).filter(Project.id == project_id).first()
    if not p:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")

    m = db.query(UserProject).filter(
        UserProject.project_id == project_id,
        UserProject.user_id == member_user_id,
    ).first()
    if not m:
        raise HTTPException(status_code=404, detail="Membresía no encontrada")

    # admin/superAdmin: pasa siempre
    if user.can_manage_all():
        db.delete(m)
        db.commit()
        return {"message": "Miembro eliminado del proyecto"}

    # jefeProyecto: reglas adicionales
    if user.role == "jefeProyecto" and _is_leader_of(db, user.user_id, project_id):
        # No puede quitarse a sí mismo
        if member_user_id == user.user_id:
            raise HTTPException(
                status_code=403,
                detail="No puedes quitarte a ti mismo del proyecto. Un admin debe hacerlo.",
            )
        # No puede quitar a otros jefes
        member_role = db.query(Role).filter(Role.id == m.project_role_id).first()
        if member_role and member_role.role_name == "jefeProyecto":
            raise HTTPException(
                status_code=403,
                detail="Un jefeProyecto no puede quitar a otro jefe. Solo admin/superAdmin pueden.",
            )
        db.delete(m)
        db.commit()
        return {"message": "Miembro eliminado del proyecto"}

    raise HTTPException(status_code=403, detail="No autorizado")
