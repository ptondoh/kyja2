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

# Une même page déclenche plusieurs requêtes en parallèle (images, HTMX) avec le même jeton de
# renouvellement. Dans ce court délai après une rotation, le rejouer est bénin : ce n'est pas un vol.
CONCURRENCY_GRACE = timedelta(seconds=15)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cookie_args() -> dict:
    return {"httponly": True, "secure": not get_settings().is_dev, "samesite": "strict"}


def session_cookies(user: User, refresh_raw: str | None) -> list[tuple[str, str, dict]]:
    """Cookies de session à poser : (nom, valeur, options). Accès (15 min) et, si fourni, renouvellement (30 j)."""
    s = get_settings()
    cookies = [
        (
            ACCESS_COOKIE,
            create_access_token(user.id, user.role.value),
            {"max_age": s.access_token_minutes * 60, "path": "/", **_cookie_args()},
        )
    ]
    if refresh_raw:
        cookies.append(
            (
                REFRESH_COOKIE,
                refresh_raw,
                {
                    "max_age": s.refresh_token_days * 86400,
                    # Envoyé à toutes les routes : c'est ce qui permet le renouvellement silencieux (voir
                    # session_refresh.py). HttpOnly + SameSite=Strict : illisible par JavaScript, jamais
                    # envoyé depuis un autre site.
                    "path": "/",
                    **_cookie_args(),
                },
            )
        )
    return cookies


def set_session_cookies(response: Response, user: User, refresh_raw: str | None) -> None:
    for key, value, options in session_cookies(user, refresh_raw):
        response.set_cookie(key, value, **options)


def _new_refresh_row(db: Session, user_id: int) -> tuple[str, RefreshToken]:
    raw = new_refresh_token()
    row = RefreshToken(
        user_id=user_id,
        token_hash=hash_refresh_token(raw),
        expires_at=_now() + timedelta(days=get_settings().refresh_token_days),  # fenêtre glissante
    )
    db.add(row)
    return raw, row


def issue_session(db: Session, user: User, response: Response) -> None:
    """Crée le couple access (15 min) + refresh (30 j) et les pose en cookies HttpOnly."""
    raw, _ = _new_refresh_row(db, user.id)
    db.commit()
    set_session_cookies(response, user, raw)


def clear_session(response: Response) -> None:
    response.delete_cookie(ACCESS_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/")
    response.delete_cookie(REFRESH_COOKIE, path="/auth")  # ancien chemin du cookie (sessions antérieures)


def revoke_all_tokens(db: Session, user_id: int) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=_now())
    )
    db.commit()


def _usable(user: User | None) -> bool:
    return bool(user and user.is_active and not user.anonymized_at)


def rotate_refresh_token(db: Session, raw_token: str) -> User | None:
    """`POST /auth/refresh` (strict). Valide et invalide le refresh token. Un jeton déjà révoqué
    rejoué = vol présumé : toutes les sessions de l'utilisateur sont révoquées."""
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
    return user if _usable(user) else None


def silent_refresh(db: Session, raw_token: str) -> tuple[User, str | None] | None:
    """Renouvellement automatique quand le cookie d'accès a expiré (voir `session_refresh.py`).

    Retourne (utilisateur, nouveau_refresh) ; le nouveau refresh vaut None pour une requête
    simultanée tolérée (elle reçoit seulement un nouveau cookie d'accès). Retourne None si le jeton est
    inconnu, expiré, ou si son rejeu indique un vol (toutes les sessions sont alors révoquées).
    """
    now = _now()
    row = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token))
    )
    if not row or row.expires_at <= now:
        return None
    user = db.get(User, row.user_id)
    if not _usable(user):
        return None
    if row.revoked_at is not None:
        # Tolérance UNIQUEMENT pour un jeton remplacé par une rotation très récente dont le successeur est
        # toujours valable (ou lui-même simplement remplacé). Un jeton révoqué par une déconnexion, un
        # « déconnexion partout » ou une alerte de vol n'a pas de successeur : jamais toléré.
        successor = db.get(RefreshToken, row.replaced_by_id) if row.replaced_by_id else None
        benign = (
            successor is not None
            and (successor.revoked_at is None or successor.replaced_by_id is not None)
            and now - row.revoked_at <= CONCURRENCY_GRACE
        )
        if benign:
            return user, None  # requête simultanée de la même page
        revoke_all_tokens(db, row.user_id)  # jeton rejoué : vol présumé
        return None
    new_raw, new_row = _new_refresh_row(db, user.id)
    db.flush()  # attribue l'identifiant du successeur
    row.revoked_at = now  # rotation : l'ancien jeton ne sert qu'une fois
    row.replaced_by_id = new_row.id
    db.commit()
    return user, new_raw


def revoke_refresh_token(db: Session, raw_token: str) -> None:
    row = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(raw_token))
    )
    if row and row.revoked_at is None:
        row.revoked_at = _now()
        db.commit()
