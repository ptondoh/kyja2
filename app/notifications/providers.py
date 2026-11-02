"""Envoi réel des courriels (Resend) et des SMS (Twilio).

Tant que les clés ne sont pas configurées, rien ne part : le message est enregistré en « aperçu »
(visible dans Admin > Messages envoyés) pour pouvoir tout tester sans compte externe.
"""
import html
import re
from dataclasses import dataclass

import httpx

from app.config import get_settings


class DeliveryError(Exception):
    """Le fournisseur a refusé ou n'a pas répondu ; le message est conservé pour une nouvelle tentative."""


@dataclass
class SendResult:
    status: str  # sent | preview
    provider: str  # resend | twilio | preview
    ref: str | None = None


URL_RE = re.compile(r"(https?://[^\s<]+)")


def text_to_html(text: str) -> str:
    """Courriel HTML minimal à partir du texte : tout est échappé, les adresses deviennent des liens."""
    escaped = html.escape(text)
    linked = URL_RE.sub(r'<a href="\1">\1</a>', escaped)
    return f'<div style="font-family:Arial,sans-serif;font-size:16px;line-height:1.5">{linked.replace(chr(10), "<br>")}</div>'


def normalize_phone(raw: str) -> str | None:
    """Numéro nord-américain en format international (E.164). None si le numéro n'est pas utilisable."""
    digits = re.sub(r"\D", "", raw or "")
    if raw and raw.strip().startswith("+") and 8 <= len(digits) <= 15:
        return "+" + digits
    if len(digits) == 10:
        return "+1" + digits
    if len(digits) == 11 and digits.startswith("1"):
        return "+" + digits
    return None


def email_configured() -> bool:
    return bool(get_settings().resend_api_key)


def sms_configured() -> bool:
    s = get_settings()
    return bool(s.twilio_account_sid and s.twilio_auth_token and s.twilio_from)


def send_email(to: str, subject: str, body: str) -> SendResult:
    s = get_settings()
    if not s.resend_api_key:
        return SendResult("preview", "preview")
    import resend

    resend.api_key = s.resend_api_key
    try:
        result = resend.Emails.send(
            {"from": s.email_from, "to": [to], "subject": subject, "text": body, "html": text_to_html(body)}
        )
    except Exception as exc:  # noqa: BLE001 - toute erreur du fournisseur devient un échec réessayable
        raise DeliveryError(f"Resend : {exc}"[:480]) from exc
    ref = result.get("id") if isinstance(result, dict) else getattr(result, "id", None)
    return SendResult("sent", "resend", ref)


def send_sms(to: str, body: str) -> SendResult:
    s = get_settings()
    if not sms_configured():
        return SendResult("preview", "preview")
    number = normalize_phone(to)
    if not number:
        raise DeliveryError("Numéro de téléphone invalide pour un SMS.")
    try:
        response = httpx.post(
            f"https://api.twilio.com/2010-04-01/Accounts/{s.twilio_account_sid}/Messages.json",
            data={"To": number, "From": s.twilio_from, "Body": body},
            auth=(s.twilio_account_sid, s.twilio_auth_token),
            timeout=10.0,
        )
    except httpx.HTTPError as exc:
        raise DeliveryError(f"Twilio injoignable : {exc}"[:480]) from exc
    if response.status_code not in (200, 201):
        detail = ""
        try:
            detail = response.json().get("message", "")
        except ValueError:
            pass
        raise DeliveryError(f"Twilio {response.status_code} : {detail}"[:480])
    return SendResult("sent", "twilio", response.json().get("sid"))
