"""Avertir les personnes : alerte dans le site, courriel et SMS, selon le choix de chacune."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.db.base import SessionLocal
from app.db.models.core import Appointment, MessageOutbox, Notification, Role, User
from app.notifications import providers
from app.tasks import dispatch

# Adresses de remplacement créées par le système : on n'y envoie jamais de courriel.
UNDELIVERABLE_SUFFIXES = ("@sans-courriel.kyja", "@supprime.kyja")
MAX_ATTEMPTS = 3


def now() -> datetime:
    return datetime.now(timezone.utc)


def has_real_email(user: User) -> bool:
    return not user.email.lower().endswith(UNDELIVERABLE_SUFFIXES)


def available_channels(user: User) -> dict[str, bool]:
    """Ce qui est réellement possible pour cette personne (affiché dans son profil)."""
    return {
        "email": has_real_email(user),
        "sms": bool(user.phone and providers.normalize_phone(user.phone)),
    }


def notify_user(
    db: Session,
    user: User,
    *,
    kind: str,
    title: str,
    body: str,
    url: str | None = None,
    appointment: Appointment | None = None,
    email_subject: str | None = None,
    email_body: str | None = None,
    sms_body: str | None = None,
) -> list[int]:
    """Crée l'alerte du site et met les courriels/SMS en file selon les préférences. Retourne les identifiants à envoyer."""
    is_client = user.role == Role.client
    if user.anonymized_at or not user.is_active:
        return []
    ids: list[int] = []
    if user.pref_in_app or not is_client:  # le personnel reçoit toujours les alertes du site
        db.add(Notification(user_id=user.id, kind=kind, title=title[:200], body=body[:1000], url=url, appointment_id=appointment.id if appointment else None))
    if user.pref_email and email_subject and email_body and has_real_email(user):
        ids.append(_queue(db, user, "email", user.email, email_subject, email_body, appointment))
    if user.pref_sms and sms_body and user.phone:
        ids.append(_queue(db, user, "sms", user.phone, None, sms_body, appointment))
    db.commit()
    return ids


def _queue(db: Session, user: User, channel: str, to: str, subject: str | None, body: str, appointment: Appointment | None) -> int:
    row = MessageOutbox(
        user_id=user.id, channel=channel, to_address=to[:255], subject=subject[:200] if subject else None,
        body=body, appointment_id=appointment.id if appointment else None,
    )  # fmt: skip
    db.add(row)
    db.flush()
    return row.id


def staff_for(db: Session, appointment: Appointment) -> list[tuple[User, str]]:
    """Qui prévenir dans l'équipe : les administrateurs et la coiffeuse du rendez-vous, avec la page à ouvrir."""
    admins = db.scalars(select(User).where(User.role == Role.admin, User.is_active.is_(True))).all()
    people = [(a, "/admin/appointments") for a in admins]
    hairdresser_user = appointment.hairdresser.user
    if hairdresser_user.is_active and hairdresser_user.role == Role.hairdresser:
        people.append((hairdresser_user, f"/agenda/{appointment.id}"))
    return people


def notify_staff(db: Session, appointment: Appointment, *, kind: str, title: str, body: str, exclude_user_id: int | None = None) -> None:
    queued: list[int] = []
    for person, url in staff_for(db, appointment):
        if person.id != exclude_user_id:
            queued += notify_user(
                db, person, kind=kind, title=title, body=body, url=url, appointment=appointment,
                email_subject=f"{title} — KYJA BEAUTÉ", email_body=f"{body}\n\nOuvrir : {providers_base_url()}{url}", sms_body=f"KYJA : {title}. {body}"[:300],
            )  # fmt: skip
    dispatch.send_messages(queued, threaded=True)  # courriel / SMS selon les choix de chaque personne, sans retarder la réponse


def providers_base_url() -> str:
    from app.config import get_settings

    return get_settings().public_base_url.rstrip("/")


# --- envoi -----------------------------------------------------------------------------------------------------------


def deliver(db: Session, ids: list[int]) -> list[int]:
    """Envoie les messages en file. Un échec est noté (et réessayable), il ne bloque jamais les autres.

    Retourne les identifiants à réessayer plus tard (échec, tentatives restantes).
    """
    retry: list[int] = []
    for row in db.scalars(select(MessageOutbox).where(MessageOutbox.id.in_(ids), MessageOutbox.status.in_(("queued", "failed")))):
        if row.attempts >= MAX_ATTEMPTS and row.status == "failed":
            continue
        row.attempts += 1
        try:
            result = providers.send_email(row.to_address, row.subject or "", row.body) if row.channel == "email" else providers.send_sms(row.to_address, row.body)
        except providers.DeliveryError as exc:
            row.status, row.error = "failed", str(exc)
        else:
            row.status, row.provider, row.provider_ref, row.error = ("sent" if result.status == "sent" else "preview"), result.provider, result.ref, None
            row.sent_at = now()
        if row.status == "failed" and row.attempts < MAX_ATTEMPTS:
            retry.append(row.id)
        db.commit()
    return retry


def retry_stuck(db: Session, older_than_minutes: int = 10) -> int:
    """Messages restés « en file » (processus arrêté en plein envoi) : on les envoie maintenant. Retourne leur nombre."""
    cutoff = now() - timedelta(minutes=older_than_minutes)
    ids = list(db.scalars(select(MessageOutbox.id).where(MessageOutbox.status == "queued", MessageOutbox.created_at < cutoff)))
    if ids:
        deliver(db, ids)
    return len(ids)


def deliver_in_background(ids: list[int]) -> None:
    """À donner à `BackgroundTasks` : ouvre sa propre session, après la réponse à la requête."""
    if not ids:
        return
    with SessionLocal() as db:
        deliver(db, ids)


# --- alertes du site -------------------------------------------------------------------------------------------------


def unread_count(db: Session, user_id: int) -> int:
    return db.scalar(select(func.count()).select_from(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None))) or 0


def mark_all_read(db: Session, user_id: int) -> None:
    db.execute(update(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None)).values(read_at=now()))
    db.commit()
