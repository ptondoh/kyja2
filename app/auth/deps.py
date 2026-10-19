from urllib.parse import quote

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.db.base import get_db
from app.db.models.core import Role, User

ACCESS_COOKIE = "access_token"
REFRESH_COOKIE = "refresh_token"


def get_current_user_optional(request: Request, db: Session = Depends(get_db)) -> User | None:
    token = request.cookies.get(ACCESS_COOKIE)
    payload = decode_access_token(token) if token else None
    if not payload:
        return None
    user = db.get(User, int(payload["sub"]))
    if not user or not user.is_active or user.anonymized_at:
        return None
    return user


def require_user(user: User | None = Depends(get_current_user_optional)) -> User:
    if not user:
        raise HTTPException(status_code=401, detail="Authentification requise")
    return user


def require_role(*roles: Role):
    """Contrôle d'accès par rôle. La propriété de la ressource se vérifie dans chaque route."""

    def checker(user: User = Depends(require_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status_code=403, detail="Accès refusé")
        return user

    return checker


def require_login(request: Request, user: User | None = Depends(get_current_user_optional)) -> User:
    """Pour les pages HTML : redirige vers la connexion puis revient sur la page demandée."""
    if not user:
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        raise HTTPException(status_code=303, headers={"Location": f"/auth/login?next={quote(target)}"})
    return user
