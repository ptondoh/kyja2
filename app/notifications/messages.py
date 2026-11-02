"""Textes des messages (courriel, SMS, alerte du site). Tout est écrit en français, sur un ton simple."""
from datetime import datetime
from decimal import Decimal

from app.config import get_settings
from app.db.models.core import Appointment
from app.templating import fr_date, fr_time, money

SALON = "KYJA BEAUTÉ"


def base_url() -> str:
    return get_settings().public_base_url.rstrip("/")


def _when(appt: Appointment) -> str:
    return f"{fr_date(appt.starts_at)} à {fr_time(appt.starts_at)}"


def _first_name(full_name: str) -> str:
    return (full_name or "").split(" ")[0] or full_name


def quote_link(token: str) -> str:
    return f"{base_url()}/quotes/{token}"


def quote_ready(appt: Appointment, price: Decimal, link: str, valid_until: datetime) -> dict:
    """Message envoyé à la cliente quand le prix final de sa demande sur mesure est prêt."""
    when, amount = _when(appt), money(price)
    who = _first_name(appt.client.full_name)
    return {
        "title": f"Votre prix est prêt : {amount}",
        "body": f"Confirmez ou refusez votre rendez-vous du {when} ({appt.service.name}).",
        "email_subject": f"{SALON} : votre prix est prêt ({amount})",
        "email_body": (
            f"Bonjour {who},\n\n"
            f"Le salon a étudié votre demande « {appt.service.name} » du {when}, avec {appt.hairdresser.user.full_name}.\n\n"
            f"Prix final proposé : {amount}\n\n"
            f"Pour confirmer ou refuser, ouvrez ce lien :\n{link}\n\n"
            f"Ce lien est valable jusqu'au {fr_date(valid_until)}. Si ce prix ne vous convient pas, vous pouvez refuser : "
            f"votre rendez-vous sera simplement annulé.\n\n"
            f"{SALON}\n"
            f"Pour choisir comment recevoir nos messages : {base_url()}/clients/me"
        ),
        "sms_body": f"{SALON} : votre prix pour le {when} est de {amount}. Confirmer ou refuser : {link}",
    }


def quote_reminder_title(appt: Appointment) -> str:
    return f"Prix à confirmer pour le {_when(appt)}"


def staff_new_request(appt: Appointment) -> tuple[str, str]:
    kind = "demande sur mesure à chiffrer" if appt.booking_mode.value == "custom" else "nouveau rendez-vous"
    return (f"Nouvelle {kind}" if appt.booking_mode.value == "custom" else "Nouveau rendez-vous", f"{_first_name(appt.client.full_name)} · {appt.service.name} · {_when(appt)}")


def staff_quote_decision(appt: Appointment, accepted: bool) -> tuple[str, str]:
    amount = money(appt.quoted_price)
    who = _first_name(appt.client.full_name)
    if accepted:
        return (f"Prix accepté : {amount}", f"{who} a confirmé le rendez-vous du {_when(appt)} ({appt.service.name}).")
    return ("Prix refusé : rendez-vous annulé", f"{who} a refusé {amount} pour le rendez-vous du {_when(appt)} ({appt.service.name}). Le créneau est libéré.")


def staff_quote_expired(appt: Appointment) -> tuple[str, str]:
    return ("Prix sans réponse : rendez-vous annulé", f"{_first_name(appt.client.full_name)} n'a pas répondu à temps pour le {_when(appt)} ({appt.service.name}). Le créneau est libéré.")


def staff_cancelled_by_client(appt: Appointment) -> tuple[str, str]:
    return ("Rendez-vous annulé par la cliente", f"{_first_name(appt.client.full_name)} a annulé le rendez-vous du {_when(appt)} ({appt.service.name}).")
