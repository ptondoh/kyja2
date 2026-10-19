from datetime import datetime, timedelta, timezone

from fastapi import Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth.deps import ACCESS_COOKIE, REFRESH_COOKIE
from app.auth.security import (
    create_access_token,
    hash_refresh_token,
    new_refresh_token,
)
from app.config import get_settings
from app.db.models.core import RefreshToken, User


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cookie_args() -> dict:
    return {"httponly": True, "secure": not get_settings().is_dev, "samesite": "strict"}


def issue_session(db: Session, user: User, response: Response) -> None:
    """Crée le couple access (15 min) + refresh (30 j) et les pose en cookies HttpOnly."""
    s = get_settings()
    refresh = new_refresh_token()
    db.add(
        RefreshToken(
            user_id=user.id,
            token_hash=hash_refresh_token(refresh),
            expires_at=_now() + timedelta(days=s.refresh_token_days),
        )
    )
    db.commit()
    response.set_cookie(
        ACCESS_COOKIE,
        create_access_token(user.id, user.role.value),
        max_age=s.access_token_minutes * 60,
        path="/",
        **_cookie_args(),
    )
    response.set_cookie(
        REFRESH_COOKIE,
        refresh,
        max_age=s.refresh_token_days * 86400,
        path="/auth",  # envoyé uniquement aux routes /auth/*
        **_cookie_args(),
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/auth")


def revoke_all_tokens(db: Session, user_id: int) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    db.commit()


def rotate_refresh_token(db: Session, raw_token: str) -> User | None:
    """Valide et invalide le refresh token. Un jeton déjà révoqué rejoué = vol présumé :
    toutes les sessions de l'utilisateur sont révoquées."""
    row = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token))
    )
    if not row:
        return None
    if row.revoked_at is not None:
        revoke_all_tokens(db, row.user_id)
        return None
    if row.expires_at <= _now():
        return None
    row.revoked_at = _now()
    db.commit()
    user = db.get(User, row.user_id)
    if not user or not user.is_active or user.anonymized_at:
        return None
    return user


def revoke_refresh_token(db: Session, raw_token: str) -> None:
    row = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token))
    )
    if row and row.revoked_at is None:
        row.revoked_at = _now()
        db.commit()
