from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.bookings.engine import salon_tz
from app.salon import salon_phone

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

_JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_MOIS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
         "septembre", "octobre", "novembre", "décembre"]  # fmt: skip


def fr_date(dt: datetime | date) -> str:
    # un datetime (UTC) est converti à l'heure du salon ; une date simple est déjà « locale »
    local = dt.astimezone(salon_tz()) if isinstance(dt, datetime) else dt
    return f"{_JOURS[local.weekday()]} {local.day} {_MOIS[local.month - 1]} {local.year}"


def fr_month(d) -> str:
    return f"{_MOIS[d.month - 1]} {d.year}"


def fr_time(dt: datetime) -> str:
    local = dt.astimezone(salon_tz())
    return f"{local.hour} h" if local.minute == 0 else f"{local.hour} h {local.minute:02d}"


def fr_duration(minutes: int) -> str:
    h, m = divmod(int(minutes), 60)
    if h and m:
        return f"{h} h {m:02d}"
    return f"{h} h" if h else f"{m} min"


def money(value: Decimal | None) -> str:
    return "—" if value is None else f"{value:.2f} $ CAD"


STATUS_LABELS = {
    "pending_quote": "Prix à proposer",
    "awaiting_client": "En attente de la cliente",
    "confirmed": "Confirmé",
    "completed": "Terminé",
    "cancelled": "Annulé",
}
CANCEL_REASONS = {
    "client": "annulé par la cliente",
    "quote_declined": "prix refusé par la cliente",
    "quote_expired": "prix sans réponse",
    "account_deleted": "compte supprimé",
    "hairdresser_removed": "coiffeuse retirée",
    "admin": "annulé par le salon",
}


def status_label(appt) -> str:
    return STATUS_LABELS[appt.status.value]


def cancel_reason(appt) -> str:
    return CANCEL_REASONS.get(appt.cancel_reason or "", "")


templates.env.filters.update(fr_date=fr_date, fr_month=fr_month, fr_time=fr_time, fr_duration=fr_duration, money=money)
templates.env.globals["weekdays"] = _JOURS
templates.env.globals["status_label"] = status_label
templates.env.globals["cancel_reason"] = cancel_reason
templates.env.globals["salon_phone"] = salon_phone


def render(request: Request, name: str, status_code: int = 200, **context):
    """Rend un template en injectant le nonce CSP et le jeton CSRF de la requête."""
    context.setdefault("csp_nonce", getattr(request.state, "csp_nonce", ""))
    context.setdefault("csrf_token", getattr(request.state, "csrf_token", ""))
    return templates.TemplateResponse(request, name, context, status_code=status_code)


def page_url(request: Request, page: int) -> str:
    """Adresse de la même liste à une autre page, filtres conservés."""
    url = request.url.include_query_params(page=page)
    return f"{url.path}?{url.query}"


templates.env.globals["page_url"] = page_url
