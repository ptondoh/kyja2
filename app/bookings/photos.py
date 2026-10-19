"""Photos d'inspiration des demandes sur mesure (PRD 4.A, mode 2) : jusqu'à 10 par demande.

Données personnelles : stockage privé, EXIF supprimé (géolocalisation des photos de téléphone),
brouillons purgés après 24 h, accès réservé à la cliente (ou à la visiteuse qui les a envoyées
avant de se connecter), à l'admin et à la coiffeuse assignée.
"""
import io
import uuid
from datetime import datetime, timedelta, timezone

import magic
from fastapi import UploadFile
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.auth.ratelimit import RateLimiter
from app.bookings.drafts import Owner
from app.config import get_settings
from app.db.models.core import Appointment, BookingPhoto, Hairdresser, Role, User
from app.media.storage import get_private_storage

MAX_PHOTOS = 10
ALLOWED_MIMES = {"image/jpeg", "image/png", "image/webp"}
MAX_SIDE = 1600
CHUNK = 1024 * 1024
DRAFT_TTL = timedelta(hours=24)

# 60 envois par heure et par propriétaire : évite qu'un compte (ou un jeton) remplisse le stockage.
upload_limiter = RateLimiter(max_hits=60, window_seconds=3600)
# Visiteuses non connectées : en plus, plafond par adresse IP.
anon_ip_limiter = RateLimiter(max_hits=30, window_seconds=3600)


class PhotoRejected(ValueError):
    """Photo refusée ; le message est affichable à la cliente."""


def _owned_by(owner: Owner):
    if owner.user_id:
        return BookingPhoto.client_id == owner.user_id
    return BookingPhoto.draft_token_hash == owner.token_hash


def drafts(db: Session, owner: Owner | None) -> list[BookingPhoto]:
    if owner is None:
        return []
    return list(
        db.scalars(
            select(BookingPhoto)
            .where(_owned_by(owner), BookingPhoto.appointment_id.is_(None))
            .order_by(BookingPhoto.id)
        )
    )


def _read_capped(file: UploadFile, max_bytes: int) -> bytes:
    buf = bytearray()
    while chunk := file.file.read(CHUNK):
        buf += chunk
        if len(buf) > max_bytes:
            raise PhotoRejected(f"Photo trop volumineuse (max {max_bytes // 2**20} Mo).")
    if not buf:
        raise PhotoRejected("Fichier vide.")
    return bytes(buf)


def add_draft(
    db: Session, owner: Owner, file: UploadFile, now: datetime | None = None, ip: str | None = None
) -> BookingPhoto:
    """Valide, nettoie et enregistre une photo en brouillon. Lève PhotoRejected si refusée."""
    if upload_limiter.is_limited(owner.key) or (ip and not owner.user_id and anon_ip_limiter.is_limited(ip)):
        raise PhotoRejected("Trop de photos envoyées. Réessayez plus tard.")
    purge_stale(db, now)
    if len(drafts(db, owner)) >= MAX_PHOTOS:
        raise PhotoRejected(f"Maximum {MAX_PHOTOS} photos par demande.")
    upload_limiter.hit(owner.key)
    if ip and not owner.user_id:
        anon_ip_limiter.hit(ip)

    raw = _read_capped(file, get_settings().image_max_mb * 2**20)
    if magic.from_buffer(raw[:4096], mime=True) not in ALLOWED_MIMES:  # contenu réel, jamais l'extension
        raise PhotoRejected("Type de fichier non autorisé (JPEG, PNG ou WebP).")
    try:
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
            im = ImageOps.exif_transpose(im).convert("RGB")  # ré-encodage : plus aucune métadonnée EXIF
            im.thumbnail((MAX_SIDE, MAX_SIDE))
            out = io.BytesIO()
            im.save(out, "WEBP", quality=82)
            width, height = im.size
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        raise PhotoRejected("Image illisible ou corrompue.") from None

    data = out.getvalue()
    folder = f"u{owner.user_id}" if owner.user_id else "anon"
    storage_key = f"booking-photos/{folder}/{uuid.uuid4().hex}.webp"
    get_private_storage().save(data, storage_key)
    photo = BookingPhoto(
        client_id=owner.user_id, draft_token_hash=owner.token_hash,
        storage_key=storage_key, width=width, height=height, size_bytes=len(data),
    )  # fmt: skip
    db.add(photo)
    db.commit()
    return photo


def delete_photo(db: Session, photo: BookingPhoto) -> None:
    get_private_storage().delete(photo.storage_key)
    db.delete(photo)
    db.commit()


def purge_stale(db: Session, now: datetime | None = None) -> int:
    """Supprime les brouillons abandonnés (fichier et ligne). Retourne le nombre purgé."""
    cutoff = (now or datetime.now(timezone.utc)) - DRAFT_TTL
    stale = db.scalars(
        select(BookingPhoto).where(BookingPhoto.appointment_id.is_(None), BookingPhoto.created_at < cutoff)
    ).all()
    storage = get_private_storage()
    for p in stale:
        storage.delete(p.storage_key)
    if stale:
        db.execute(delete(BookingPhoto).where(BookingPhoto.id.in_([p.id for p in stale])))
        db.commit()
    return len(stale)


def can_view(db: Session, user: User | None, token_hash: str | None, photo: BookingPhoto) -> bool:
    """Propriétaire (cliente, ou visiteuse porteuse du jeton), admin, ou coiffeuse assignée au rendez-vous."""
    if token_hash and photo.draft_token_hash and photo.draft_token_hash == token_hash:
        return True
    if user is None:
        return False
    if (photo.client_id and photo.client_id == user.id) or user.role == Role.admin:
        return True
    if user.role == Role.hairdresser and photo.appointment_id:
        appt = db.get(Appointment, photo.appointment_id)
        h = db.get(Hairdresser, appt.hairdresser_id) if appt else None
        return bool(h and h.user_id == user.id)
    return False


def is_owner(photo: BookingPhoto, owner: Owner | None) -> bool:
    if owner is None:
        return False
    if owner.user_id:
        return photo.client_id == owner.user_id
    return bool(owner.token_hash and photo.draft_token_hash == owner.token_hash)


def load_for_booking(db: Session, owner: Owner | None, ids: list[int]) -> list[BookingPhoto]:
    """Brouillons du propriétaire correspondant aux identifiants reçus (ceux d'autrui sont refusés)."""
    if not ids:
        return []
    if len(ids) > MAX_PHOTOS:
        raise PhotoRejected(f"Maximum {MAX_PHOTOS} photos par demande.")
    if owner is None:
        raise PhotoRejected("Une des photos n'est plus disponible. Merci de la renvoyer.")
    found = db.scalars(
        select(BookingPhoto)
        .where(BookingPhoto.id.in_(ids), _owned_by(owner), BookingPhoto.appointment_id.is_(None))
        .order_by(BookingPhoto.id)
    ).all()
    if len(found) != len(set(ids)):
        raise PhotoRejected("Une des photos n'est plus disponible. Merci de la renvoyer.")
    return list(found)
