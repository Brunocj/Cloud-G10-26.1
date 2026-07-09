# app/services/permissions.py
"""
Autorización de negocio sobre slices (REQ-JP-07 "Modo Dios").

Regla central `can_operate_slice`:
  · dueño del slice                      → sí
  · admin / superAdmin                   → sí (control transversal)
  · jefeProyecto líder del proyecto al
    que pertenece el slice               → sí (intervención directa: consola,
                                            modificación y destrucción de los
                                            slices de sus alumnos)
"""
from sqlalchemy.orm import Session

from app.auth import CurrentUser
from app.models import Role, Slice, UserProject


def is_project_leader(db: Session, user_id: str, project_id: int) -> bool:
    """True si user_id es jefeProyecto (rol contextual) del proyecto dado."""
    if project_id is None:
        return False
    jefe_role = db.query(Role).filter(Role.role_name == "jefeProyecto").first()
    if not jefe_role:
        return False
    return db.query(UserProject).filter(
        UserProject.user_id == user_id,
        UserProject.project_id == project_id,
        UserProject.project_role_id == jefe_role.id,
    ).first() is not None


def led_project_ids(db: Session, user_id: str) -> list[int]:
    """IDs de proyectos donde el usuario es jefeProyecto."""
    jefe_role = db.query(Role).filter(Role.role_name == "jefeProyecto").first()
    if not jefe_role:
        return []
    memberships = db.query(UserProject).filter(
        UserProject.user_id == user_id,
        UserProject.project_role_id == jefe_role.id,
    ).all()
    return [m.project_id for m in memberships]


def can_operate_slice(db: Session, user: CurrentUser, db_slice: Slice) -> bool:
    """Dueño, admin+, o jefeProyecto del proyecto del slice."""
    if user.user_id == db_slice.creator_id:
        return True
    if user.can_manage_all():
        return True
    if user.role == "jefeProyecto":
        return is_project_leader(db, user.user_id, db_slice.project_id)
    return False
