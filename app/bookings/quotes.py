"""Prix final d'une demande sur mesure : proposé par le salon, accepté ou refusé par la cliente.

    pending_quote --(le salon propose un prix)--> awaiting_client --(la cliente accepte)--> confirmed
                                                       |--(refus ou délai dépassé)--> cancelled

Le créneau reste réservé tout du long. Le lien envoyé à la cliente contient un jeton aléatoire à usage
unique (seule son empreinte est en base) : il permet uniquement d'accepter ou de refuser CE rendez-vous.
"""
import hashlib
import secrets
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.bookings.engine import BookingError, utcnow
from app.db.models.core import Appointment, AppointmentStatus, BookingMode, Hairdresser, User
from app.db.models.settings import get_int_setting
from app.notifications import messages
from app.notifications import service as notifications

MAX_PRICE = Decimal("99999.99")


class QuoteError(BookingError):
    """Le prix ne peut pas être proposé ou décidé dans l'état actuel ; le message est affichable."""


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def propose_quote(db: Session, appt: Appointment, price: Decimal, by_user: User, now: datetime | None = None) -> str:
    """Enregistre le prix proposé et retourne le jeton du lien (à envoyer, il n'est stocké nulle part en clair)."""
    now = now or utcnow()
    if appt.booking_mode != BookingMode.custom:
        raise QuoteError("Seules les demandes sur mesure ont un prix à proposer.")
    if appt.status not in (AppointmentStatus.pending_quote, AppointmentStatus.awaiting_client):
        raise QuoteError("Ce rendez-vous n'attend pas de prix.")
    if appt.starts_at <= now:
        raise QuoteError("Ce rendez-vous est déjà commencé ou passé.")
    if price <= 0 or price > MAX_PRICE or price != price.quantize(Decimal("0.01")):
        raise QuoteError("Prix invalide (entre 0,01 et 99 999,99, deux décimales au plus).")
    token = secrets.token_urlsafe(32)
    validity = timedelta(days=get_int_setting(db, "quote_validity_days"))
    appt.quoted_price = price
    appt.quote_token_hash = _hash(token)  # un nouveau prix remplace (et invalide) le lien précédent
    appt.quote_proposed_by_id, appt.quote_proposed_at, appt.quote_decided_at = by_user.id, now, None
    appt.quote_expires_at = min(now + validity, appt.starts_at)
    appt.status = AppointmentStatus.awaiting_client
    db.commit()
    return token


def propose_and_notify(db: Session, appt: Appointment, price: Decimal, by_user: User, now: datetime | None = None) -> list[int]:
    """Propose le prix puis avertit la cliente par les canaux qu'elle a choisis. Retourne les messages à envoyer."""
    token = propose_quote(db, appt, price, by_user, now)
    text = messages.quote_ready(appt, price, messages.quote_link(token), appt.quote_expires_at)
    return notifications.notify_user(
        db, appt.client, kind="quote_ready", title=text["title"], body=text["body"], url=f"/bookings/{appt.id}", appointment=appt,
        email_subject=text["email_subject"], email_body=text["email_body"], sms_body=text["sms_body"],
    )  # fmt: skip


def find_by_token(db: Session, token: str) -> Appointment | None:
    if not token or len(token) > 100:
        return None
    return db.scalar(
        select(Appointment)
        .options(selectinload(Appointment.service), selectinload(Appointment.client), selectinload(Appointment.hairdresser).selectinload(Hairdresser.user))
        .where(Appointment.quote_token_hash == _hash(token))
    )


def is_open(appt: Appointment, now: datetime | None = None) -> bool:
    """Peut-on encore répondre ? (en attente de la cliente, lien pas expiré, rendez-vous pas commencé)"""
    now = now or utcnow()
    return (
        appt.status == AppointmentStatus.awaiting_client
        and appt.quote_expires_at is not None
        and appt.quote_expires_at > now
        and appt.starts_at > now
    )


def decide(db: Session, appt_id: int, accept: bool, now: datetime | None = None) -> Appointment:
    """La cliente accepte ou refuse. Verrouille la ligne : deux réponses simultanées ne passent pas toutes les deux."""
    now = now or utcnow()
    appt = db.scalar(select(Appointment).where(Appointment.id == appt_id).with_for_update())
    if appt is None or not is_open(appt, now):
        db.rollback()
        raise QuoteError("Cette proposition n'est plus valable.")
    appt.quote_decided_at = now
    appt.quote_token_hash = None  # le lien ne sert qu'une fois
    if accept:
        appt.status = AppointmentStatus.confirmed
    else:
        appt.status, appt.cancel_reason = AppointmentStatus.cancelled, "quote_declined"
    db.commit()
    return appt


def expire_stale_quotes(db: Session, now: datetime | None = None) -> list[Appointment]:
    """Annule les propositions restées sans réponse (lien expiré ou rendez-vous passé) : le créneau est libéré."""
    now = now or utcnow()
    stale = db.scalars(
        select(Appointment).where(
            Appointment.status == AppointmentStatus.awaiting_client,
            (Appointment.quote_expires_at <= now) | (Appointment.starts_at <= now),
        )
    ).all()
    for a in stale:
        a.status, a.cancel_reason, a.quote_token_hash = AppointmentStatus.cancelled, "quote_expired", None
    if stale:
        db.commit()
    return list(stale)


def parse_price(raw: str) -> Decimal:
    """« 85 », « 85,5 » ou « 85.50 » -> Decimal. Lève QuoteError si le texte n'est pas un prix."""
    try:
        value = Decimal((raw or "").replace(",", ".").replace("$", "").strip())
    except InvalidOperation:
        raise QuoteError("Prix invalide (ex. 85 ou 85,50).") from None
    if not value.is_finite():
        raise QuoteError("Prix invalide (ex. 85 ou 85,50).")
    return value
