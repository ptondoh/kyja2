"""Profil de la cliente : consentement, export de ses données, droit à l'oubli (LPRPDE).

Le droit à l'oubli est une **anonymisation** (pas une suppression) pour préserver l'intégrité
comptable : l'historique des rendez-vous, les enregistrements financiers (7 ans) et les preuves de
consentement sont conservés ; tout ce qui identifie la personne est effacé (voir ARCHITECTURE.md).
"""
import logging
import secrets
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.auth.security import hash_password
from app.auth.service import revoke_all_tokens
from app.db.models.core import (
    Appointment,
    AppointmentStatus,
    BookingPhoto,
    ConsentRecord,
    Hairdresser,
    User,
)
from app.media.storage import get_private_storage

audit = logging.getLogger("kyja.audit")  # trace des opérations sensibles (sans donnée personnelle)

ANONYMIZED_NAME = "CLIENT_SUPPRIMÉ"
FORGET_CONFIRMATION = "SUPPRIMER"
EXPORT_FORMAT_VERSION = 1


def anonymized_email(user_id: int) -> str:
    return f"anon_{user_id}@supprime.kyja"


# --- consentement marketing (historique ajouté, jamais modifié) --------------------------------------------


def marketing_state(db: Session, user_id: int) -> ConsentRecord | None:
    """Dernier enregistrement de consentement marketing (None : jamais renseigné)."""
    return db.scalar(
        select(ConsentRecord)
        .where(ConsentRecord.user_id == user_id, ConsentRecord.kind == "marketing")
        .order_by(ConsentRecord.id.desc())
        .limit(1)
    )


def set_marketing_consent(db: Session, user: User, granted: bool) -> bool:
    """Ajoute un enregistrement SEULEMENT si l'état change. Retourne True si quelque chose a changé."""
    current = marketing_state(db, user.id)
    if current is not None and current.granted == granted:
        return False
    db.add(ConsentRecord(user_id=user.id, kind="marketing", granted=granted))
    db.commit()
    return True


# --- export des données personnelles (droit d'accès) -----------------------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def export_user_data(db: Session, user: User) -> dict:
    """Copie de toutes les données de la cliente. Exclut les secrets (mot de passe, jetons)."""
    consents = db.scalars(
        select(ConsentRecord).where(ConsentRecord.user_id == user.id).order_by(ConsentRecord.id)
    ).all()
    appointments = db.scalars(
        select(Appointment)
        .options(
            selectinload(Appointment.service),
            selectinload(Appointment.photos),
            selectinload(Appointment.hairdresser).selectinload(Hairdresser.user),
        )
        .where(Appointment.client_id == user.id)
        .order_by(Appointment.starts_at)
    ).all()
    photos = db.scalars(select(BookingPhoto).where(BookingPhoto.client_id == user.id).order_by(BookingPhoto.id)).all()
    return {
        "format_version": EXPORT_FORMAT_VERSION,
        "generated_at": _iso(datetime.now(timezone.utc)),
        "note": "Copie des données personnelles détenues par KYJA BEAUTÉ vous concernant.",
        "profile": {
            "id": user.id,
            "full_name": user.full_name,
            "email": user.email,
            "phone": user.phone,
            "address": user.address,
            "created_at": _iso(user.created_at),
        },
        "consents": [{"kind": c.kind, "granted": c.granted, "recorded_at": _iso(c.created_at)} for c in consents],
        "appointments": [
            {
                "id": a.id,
                "service": a.service.name,
                "mode": a.booking_mode.value,
                "status": a.status.value,
                "starts_at": _iso(a.starts_at),
                "ends_at": _iso(a.ends_at),
                "duration_minutes": a.computed_duration_min,
                "hairdresser": a.hairdresser.user.full_name,
                "estimated_price": str(a.estimated_price) if a.estimated_price is not None else None,
                "final_price": str(a.final_price) if a.final_price is not None else None,
                "your_description": a.client_notes,
                "photo_ids": [p.id for p in a.photos],
                "created_at": _iso(a.created_at),
            }
            for a in appointments
        ],
        "photos": [
            {
                "id": p.id,
                "appointment_id": p.appointment_id,
                "width": p.width,
                "height": p.height,
                "size_bytes": p.size_bytes,
                "uploaded_at": _iso(p.created_at),
                "download_url": f"/bookings/photos/{p.id}",  # accessible depuis votre compte
            }
            for p in photos
        ],
    }


# --- droit à l'oubli ---------------------------------------------------------------------------------------------


def forget_user(db: Session, user: User, now: datetime | None = None) -> dict:
    """Anonymise la cliente. Retourne un résumé (nombres, sans donnée personnelle).

    Anonymisé : nom, email, téléphone, adresse, mot de passe. Supprimé : photos d'inspiration (fichiers et
    lignes), descriptions des demandes, sessions. Annulé : rendez-vous à venir. Conservé : rendez-vous
    passés (référence anonymisée), enregistrements financiers, preuves de consentement.
    """
    now = now or datetime.now(timezone.utc)

    cancelled = db.execute(
        update(Appointment)
        .where(
            Appointment.client_id == user.id,
            Appointment.status == AppointmentStatus.confirmed,
            Appointment.starts_at > now,
        )
        .values(status=AppointmentStatus.cancelled)  # libère les créneaux
    ).rowcount

    # Les descriptions écrites par la cliente sont des données personnelles : effacées. Le reste de la
    # ligne (service, horaires, durée, prix) sert la comptabilité et reste.
    db.execute(update(Appointment).where(Appointment.client_id == user.id).values(client_notes=None))

    photo_keys = [p.storage_key for p in db.scalars(select(BookingPhoto).where(BookingPhoto.client_id == user.id))]
    db.execute(BookingPhoto.__table__.delete().where(BookingPhoto.client_id == user.id))

    user.full_name = ANONYMIZED_NAME
    user.email = anonymized_email(user.id)
    user.phone = None
    user.address = None
    user.password_hash = hash_password(secrets.token_urlsafe(48))  # inutilisable : personne ne le connaît
    user.is_active = False
    user.anonymized_at = now
    db.flush()
    revoke_all_tokens(db, user.id)  # valide aussi la transaction : sessions coupées partout

    storage = get_private_storage()  # fichiers effacés seulement APRÈS la validation en base
    for key in photo_keys:
        storage.delete(key)
    audit.info("account_anonymized user_id=%s photos_deleted=%s future_appointments_cancelled=%s", user.id, len(photo_keys), cancelled)
    return {"photos_deleted": len(photo_keys), "appointments_cancelled": cancelled}
