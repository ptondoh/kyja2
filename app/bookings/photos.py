"""Photos d'inspiration des demandes sur mesure (PRD 4.A, mode 2) : jusqu'à 10 par demande.

Données personnelles : stockage privé, EXIF supprimé (géolocalisation des photos de téléphone),
brouillons purgés après 24 h, accès réservé à la cliente, à l'admin et à la coiffeuse assignée.
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
from app.config import get_settings
from app.db.models.core import Appointment, BookingPhoto, Hairdresser, Role, User
from app.media.storage import get_private_storage

MAX_PHOTOS = 10
ALLOWED_MIMES = {"image/jpeg", "image/png", "image/webp"}
MAX_SIDE = 1600
CHUNK = 1024 * 1024
DRAFT_TTL = timedelta(hours=24)

# 60 envois par heure et par cliente : évite qu'un compte remplisse le stockage.
upload_limiter = RateLimiter(max_hits=60, window_seconds=3600)


class PhotoRejected(ValueError):
    """Photo refusée ; le message est affichable à la cliente."""


def drafts(db: Session, user_id: int) -> list[BookingPhoto]:
    return list(
        db.scalars(
            select(BookingPhoto)
            .where(BookingPhoto.client_id == user_id, BookingPhoto.appointment_id.is_(None))
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


def add_draft(db: Session, user: User, file: UploadFile, now: datetime | None = None) -> BookingPhoto:
    """Valide, nettoie et enregistre une photo en brouillon. Lève PhotoRejected si refusée."""
    key = f"user:{user.id}"
    if upload_limiter.is_limited(key):
        raise PhotoRejected("Trop de photos envoyées. Réessayez plus tard.")
    purge_stale(db, now)
    if len(drafts(db, user.id)) >= MAX_PHOTOS:
        raise PhotoRejected(f"Maximum {MAX_PHOTOS} photos par demande.")
    upload_limiter.hit(key)

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
    storage_key = f"booking-photos/{user.id}/{uuid.uuid4().hex}.webp"
    get_private_storage().save(data, storage_key)
    photo = BookingPhoto(client_id=user.id, storage_key=storage_key, width=width, height=height, size_bytes=len(data))
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


def can_view(db: Session, user: User, photo: BookingPhoto) -> bool:
    """La cliente propriétaire, l'admin, ou la coiffeuse assignée au rendez-vous."""
    if photo.client_id == user.id or user.role == Role.admin:
        return True
    if user.role == Role.hairdresser and photo.appointment_id:
        appt = db.get(Appointment, photo.appointment_id)
        h = db.get(Hairdresser, appt.hairdresser_id) if appt else None
        return bool(h and h.user_id == user.id)
    return False


def load_for_booking(db: Session, user: User, ids: list[int]) -> list[BookingPhoto]:
    """Brouillons de la cliente correspondant aux identifiants reçus (ceux d'autrui sont ignorés)."""
    if not ids:
        return []
    if len(ids) > MAX_PHOTOS:
        raise PhotoRejected(f"Maximum {MAX_PHOTOS} photos par demande.")
    found = db.scalars(
        select(BookingPhoto).where(
            BookingPhoto.id.in_(ids), BookingPhoto.client_id == user.id, BookingPhoto.appointment_id.is_(None)
        ).order_by(BookingPhoto.id)
    ).all()
    if len(found) != len(set(ids)):
        raise PhotoRejected("Une des photos n'est plus disponible. Merci de la renvoyer.")
    return list(found)
