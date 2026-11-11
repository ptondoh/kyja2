"""Messages promotionnels : uniquement aux clientes dont le dernier consentement « marketing » est un oui.

Chaque courriel et chaque SMS contient un lien de désabonnement (LCAP / CASL). Le lien est signé (HMAC) : il fonctionne sans connexion
et ne peut pas être deviné ni fabriqué pour une autre personne.
"""
import hashlib
import hmac
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models.core import ConsentRecord, MarketingCampaign, MessageOutbox, Role, User
from app.db.models.settings import get_setting
from app.notifications import providers
from app.notifications.service import has_real_email

SALON = "KYJA BEAUTÉ"
SUBJECT_MAX = 150
BODY_MIN, BODY_MAX = 10, 3000
SMS_MAX = 200


def latest_marketing_consent():
    """Sous-requête : le consentement marketing le plus récent de l'utilisateur (vrai, faux, ou NULL s'il n'a jamais répondu)."""
    return (
        select(ConsentRecord.granted)
        .where(ConsentRecord.user_id == User.id, ConsentRecord.kind == "marketing")
        .order_by(ConsentRecord.id.desc())
        .limit(1)
        .correlate(User)
        .scalar_subquery()
    )


def consenting_clients(db: Session) -> list[User]:
    """Les clientes actives qui ont accepté de recevoir des promotions."""
    return list(
        db.scalars(
            select(User)
            .where(User.role == Role.client, User.is_active.is_(True), User.anonymized_at.is_(None), latest_marketing_consent().is_(True))
            .order_by(User.id)
        )
    )


def email_recipients(users: list[User]) -> list[User]:
    return [u for u in users if has_real_email(u)]


def sms_recipients(users: list[User]) -> list[User]:
    """SMS promotionnel : seulement si la cliente a aussi choisi de recevoir des SMS et que son numéro est valide."""
    return [u for u in users if u.pref_sms and u.phone and providers.normalize_phone(u.phone)]


def audience(db: Session) -> dict[str, int]:
    users = consenting_clients(db)
    return {"consenting": len(users), "email": len(email_recipients(users)), "sms": len(sms_recipients(users))}


# --- lien de désabonnement -------------------------------------------------------------------------------------------


def _signature(user_id: int) -> str:
    key = get_settings().secret_key.encode()
    return hmac.new(key, f"unsubscribe:{user_id}".encode(), hashlib.sha256).hexdigest()[:32]


def unsubscribe_token(user_id: int) -> str:
    return f"{user_id}.{_signature(user_id)}"


def user_from_token(db: Session, token: str) -> User | None:
    uid, _, sig = (token or "").partition(".")
    if not uid.isdigit() or len(token) > 100 or not hmac.compare_digest(sig, _signature(int(uid))):
        return None
    return db.get(User, int(uid))


def unsubscribe_link(user: User) -> str:
    return f"{get_settings().public_base_url.rstrip('/')}/unsubscribe/{unsubscribe_token(user.id)}"


# --- textes ---------------------------------------------------------------------------------------------------------


def salon_address(db: Session) -> str:
    return get_setting(db, "salon_address").strip()


def email_body(campaign: MarketingCampaign, user: User, address: str) -> str:
    first = (user.full_name or "").split(" ")[0] or user.full_name
    return (
        f"Bonjour {first},\n\n{campaign.body.strip()}\n\n"
        f"—\nVous recevez ce courriel parce que vous avez accepté de recevoir les promotions de {SALON}.\n"
        f"Pour ne plus en recevoir : {unsubscribe_link(user)}\n"
        f"{SALON}" + (f", {address}" if address else "")
    )


def sms_body(campaign: MarketingCampaign, user: User) -> str:
    return f"{SALON} : {(campaign.sms_text or '').strip()} Se désabonner : {unsubscribe_link(user)}"


# --- création de la campagne -----------------------------------------------------------------------------------------


def validate(subject: str, body: str, sms_text: str, send_email: bool, send_sms: bool, db: Session) -> list[str]:
    errors = []
    if not (send_email or send_sms):
        errors.append("Choisissez au moins un canal : courriel ou SMS.")
    if send_email and not (1 <= len(subject.strip()) <= SUBJECT_MAX):
        errors.append(f"Le sujet est requis ({SUBJECT_MAX} caractères maximum).")
    if not (BODY_MIN <= len(body.strip()) <= BODY_MAX):
        errors.append(f"Le message doit contenir entre {BODY_MIN} et {BODY_MAX} caractères.")
    if send_sms and not (1 <= len(sms_text.strip()) <= SMS_MAX):
        errors.append(f"Le texte du SMS est requis ({SMS_MAX} caractères maximum).")
    return errors


def send_blockers(db: Session, campaign: MarketingCampaign) -> list[str]:
    """Ce qui empêche l'envoi réel (le message d'essai, lui, est toujours possible)."""
    problems = []
    if campaign.send_email and not salon_address(db):
        problems.append("Ajoutez l'adresse postale du salon dans Réglages : elle doit figurer dans chaque courriel promotionnel (LCAP / CASL).")
    return problems


def queue_campaign(db: Session, campaign: MarketingCampaign) -> list[int]:
    """Met en file un message par personne et par canal. Retourne les identifiants à envoyer."""
    users = consenting_clients(db)
    address = salon_address(db)
    ids: list[int] = []
    people: set[int] = set()
    if campaign.send_email:
        for u in email_recipients(users):
            row = MessageOutbox(user_id=u.id, channel="email", to_address=u.email[:255], subject=campaign.subject[:200], body=email_body(campaign, u, address), campaign_id=campaign.id)
            db.add(row)
            db.flush()
            ids.append(row.id)
            people.add(u.id)
    if campaign.send_sms:
        for u in sms_recipients(users):
            row = MessageOutbox(user_id=u.id, channel="sms", to_address=u.phone[:255], body=sms_body(campaign, u), campaign_id=campaign.id)
            db.add(row)
            db.flush()
            ids.append(row.id)
            people.add(u.id)
    campaign.recipient_count = len(people)
    campaign.status = "sent"
    campaign.sent_at = datetime.now(timezone.utc)
    db.commit()
    return ids


def queue_test(db: Session, campaign: MarketingCampaign, admin: User) -> list[int]:
    """Un exemplaire du courriel envoyé à l'admin seulement, marqué [ESSAI]. N'est pas compté dans la campagne."""
    row = MessageOutbox(
        user_id=admin.id, channel="email", to_address=admin.email[:255], subject=f"[ESSAI] {campaign.subject}"[:200],
        body=email_body(campaign, admin, salon_address(db)),
    )  # fmt: skip
    db.add(row)
    db.commit()
    return [row.id]
