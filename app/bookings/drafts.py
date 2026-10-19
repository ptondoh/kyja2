"""Réservation sans connexion préalable : le choix de la visiteuse est gardé le temps de la connexion.

Principe : la cliente parcourt tout le parcours sans compte. Un cookie HttpOnly (`booking_draft`)
contient un jeton aléatoire ; en base on ne garde que son empreinte. Ce jeton retrouve :
  * son choix (prestation, coiffeuse, créneau, description) dans `booking_drafts` ;
  * ses photos d'inspiration envoyées avant la connexion (`booking_photos.draft_token_hash`).
À la connexion, les photos sont rattachées à son compte et le choix est repris à l'étape de confirmation.
"""
import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import Request, Response
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.core import BookingDraft, BookingPhoto, User

COOKIE = "booking_draft"
TTL = timedelta(hours=24)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_from(request: Request) -> str | None:
    value = request.cookies.get(COOKIE)
    return value if value and 20 <= len(value) <= 100 else None


def set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE,
        token,
        max_age=int(TTL.total_seconds()),
        path="/bookings",  # envoyé uniquement aux routes de réservation
        httponly=True,
        secure=not get_settings().is_dev,
        samesite="lax",  # le retour depuis la page de connexion est une navigation du même site
    )


def clear_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/bookings")


@dataclass(frozen=True)
class Owner:
    """Propriétaire de photos en brouillon : une cliente connectée, ou une visiteuse (jeton de cookie)."""

    user_id: int | None = None
    token_hash: str | None = None

    @property
    def key(self) -> str:  # clé des limitations d'envoi
        return f"user:{self.user_id}" if self.user_id else f"anon:{self.token_hash}"


def owner_for(user: User | None, request: Request) -> Owner | None:
    if user:
        return Owner(user_id=user.id)
    token = token_from(request)
    return Owner(token_hash=hash_token(token)) if token else None


# --- brouillon du choix ----------------------------------------------------------------------


def save_draft(db: Session, token: str, payload: dict) -> None:
    digest = hash_token(token)
    row = db.scalar(select(BookingDraft).where(BookingDraft.token_hash == digest))
    if row:
        row.payload = payload
        row.created_at = datetime.now(timezone.utc)
    else:
        db.add(BookingDraft(token_hash=digest, payload=payload))
    db.commit()


def load_draft(db: Session, token: str | None, now: datetime | None = None) -> dict | None:
    if not token:
        return None
    row = db.scalar(select(BookingDraft).where(BookingDraft.token_hash == hash_token(token)))
    if not row or row.created_at < (now or datetime.now(timezone.utc)) - TTL:
        return None
    return dict(row.payload)


def delete_draft(db: Session, token: str | None) -> None:
    if token:
        db.execute(delete(BookingDraft).where(BookingDraft.token_hash == hash_token(token)))
        db.commit()


def purge_expired(db: Session, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now(timezone.utc)) - TTL
    n = db.execute(delete(BookingDraft).where(BookingDraft.created_at < cutoff)).rowcount
    db.commit()
    return n


def claim_photos(db: Session, user: User, token: str | None) -> int:
    """Rattache à la cliente les photos envoyées avant sa connexion. Retourne le nombre de photos."""
    if not token:
        return 0
    n = db.execute(
        update(BookingPhoto)
        .where(BookingPhoto.draft_token_hash == hash_token(token), BookingPhoto.client_id.is_(None))
        .values(client_id=user.id, draft_token_hash=None)
    ).rowcount
    db.commit()
    return n
