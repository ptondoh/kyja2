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


templates.env.filters.update(fr_date=fr_date, fr_month=fr_month, fr_time=fr_time, fr_duration=fr_duration, money=money)
templates.env.globals["weekdays"] = _JOURS
templates.env.globals["salon_phone"] = salon_phone


def render(request: Request, name: str, status_code: int = 200, **context):
    """Rend un template en injectant le nonce CSP et le jeton CSRF de la requête."""
    context.setdefault("csp_nonce", getattr(request.state, "csp_nonce", ""))
    context.setdefault("csrf_token", getattr(request.state, "csrf_token", ""))
    return templates.TemplateResponse(request, name, context, status_code=status_code)
