"""Parcours de réservation.

Tout le parcours est PUBLIC jusqu'au récapitulatif : la connexion n'est demandée qu'au moment de
confirmer, et le choix de la visiteuse est conservé (voir `drafts.py`).
"""
import calendar
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import get_current_user_optional, require_login
from app.auth.ratelimit import RateLimiter
from app.bookings import drafts, engine, photos
from app.bookings.engine import BookingError, SlotUnavailable
from app.bookings.photos import MAX_PHOTOS, PhotoRejected
from app.config import get_settings
from app.db.base import get_db
from app.db.models.core import (
    Appointment,
    AppointmentStatus,
    BookingPhoto,
    Hairdresser,
    PricingMode,
    Role,
    Service,
    User,
)
from app.db.models.settings import get_int_setting
from app.media.storage import get_private_storage
from app.security_middleware import verify_csrf
from app.services.router import ready_media
from app.templating import render

# Consultation du calendrier et des heures (public) : plafond par adresse IP contre l'aspiration automatique.
browse_limiter = RateLimiter(max_hits=600, window_seconds=3600)

RESUME_URL = "/bookings/resume"


async def limit_upload_size(request: Request) -> None:
    """Refuse tôt les envois trop gros (Nginx reste le vrai garde-fou)."""
    cap = (get_settings().image_max_mb + 1) * 2**20
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise HTTPException(status_code=413, detail="Photo trop volumineuse")


def _throttle_browsing(request: Request) -> None:
    ip = request.client.host if request.client else "unknown"
    if browse_limiter.is_limited(ip):
        raise HTTPException(status_code=429, detail="Trop de requêtes. Réessayez dans quelques minutes.")
    browse_limiter.hit(ip)


router = APIRouter(prefix="/bookings", dependencies=[Depends(verify_csrf)])

NOTES_MAX = 2000
CUSTOM_NOTES_MIN = 10


def _service_or_404(db: Session, service_id: int) -> Service:
    service = db.scalar(select(Service).options(selectinload(Service.media)).where(Service.id == service_id))
    if not service or not service.is_active:
        raise HTTPException(status_code=404)
    return service


def _parse_choice(raw: str) -> int | None:
    """« any » = peu importe ; sinon identifiant de coiffeuse."""
    if raw == "any":
        return None
    if raw.isdigit():
        return int(raw)
    raise HTTPException(status_code=400, detail="Coiffeuse invalide")


def _parse_start(raw: str) -> datetime:
    try:
        dt = datetime.fromisoformat(raw.split("|")[0])
    except ValueError:
        raise HTTPException(status_code=400, detail="Horaire invalide") from None
    if dt.tzinfo is None:
        raise HTTPException(status_code=400, detail="Horaire invalide")
    return dt.astimezone(timezone.utc)


def _choices(db: Session, service: Service):
    """Coiffeuses actives avec LEUR durée calculée (affichage de l'étape 2)."""
    return [(h, engine.quote_for(db, service, h)) for h in engine.active_hairdressers(db)]


def _parse_month(raw: str, first_allowed: date, last_allowed: date) -> date:
    try:
        month = date.fromisoformat(f"{raw}-01")
    except ValueError:
        month = first_allowed
    return min(max(month, first_allowed), last_allowed)


def _calendar_context(db: Session, service: Service, choice: int | None, month_raw: str = "") -> dict:
    """Données du calendrier mensuel : seuls les jours avec au moins un créneau libre sont cliquables."""
    now = engine.utcnow()
    tz = engine.salon_tz()
    today = now.astimezone(tz).date()
    horizon = (now + timedelta(days=get_int_setting(db, "booking_horizon_days"))).astimezone(tz).date()
    first_allowed, last_allowed = today.replace(day=1), horizon.replace(day=1)
    month_first = _parse_month(month_raw, first_allowed, last_allowed)

    valid_ids = {h.id for h in engine.active_hairdressers(db)}
    available = set() if (choice is not None and choice not in valid_ids) else engine.available_days(
        db, service, choice, month_first, now
    )
    weeks = [
        [
            None if d.month != month_first.month else {"date": d, "available": d in available, "today": d == today}
            for d in week
        ]
        for week in calendar.Calendar(firstweekday=0).monthdatescalendar(month_first.year, month_first.month)
    ]
    return {
        "month_first": month_first,
        "weeks": weeks,
        "prev_month": (month_first - timedelta(days=1)).replace(day=1) if month_first > first_allowed else None,
        "next_month": (month_first + timedelta(days=32)).replace(day=1) if month_first < last_allowed else None,
        "salon_open_online": engine.has_any_availability(db),
        "any_available": bool(available),
    }


def _claim(db: Session, user: User | None, request: Request) -> None:
    """À la connexion, les photos envoyées avant (jeton du cookie) deviennent celles de la cliente."""
    if user:
        drafts.claim_photos(db, user, drafts.token_from(request))


def _render_pick(request, db, user, service, status_code=200, **extra):
    """Étape 2 : coiffeuse, date, créneau (+ description et photos en mode sur mesure)."""
    mode = engine.booking_mode_for(service).value
    _claim(db, user, request)
    selected = extra.get("selected_hairdresser") or "any"
    choice = int(selected) if str(selected).isdigit() else None
    return render(
        request, "bookings/pick.html", status_code=status_code, current_user=user, service=service, mode=mode,
        choices=_choices(db, service), oob=False, **_calendar_context(db, service, choice),
        drafts=photos.drafts(db, drafts.owner_for(user, request)) if mode == "custom" else [],
        max_photos=MAX_PHOTOS, **extra,
    )  # fmt: skip


# --- Étape 1 et 2 : choix du mode puis de la coiffure -------------------------------------


@router.get("/new")
def new_booking(
    request: Request,
    mode: str | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    if mode not in ("catalog", "custom"):
        return render(request, "bookings/mode.html", current_user=user)
    wanted = PricingMode.estimate if mode == "catalog" else PricingMode.quote
    services = db.scalars(
        select(Service)
        .options(selectinload(Service.media))
        .where(
            Service.is_active.is_(True),
            Service.pricing_mode == wanted,
            Service.is_custom_request.is_(False),  # la demande intégrée n'est pas une « coiffure » du catalogue
        )
        .order_by(Service.category, Service.name)
    ).all()
    if mode == "custom" and not services:
        # Cas normal à l'installation : aucune prestation « sur devis » créée -> formulaire direct.
        return RedirectResponse(f"/bookings/new/{engine.custom_request_service(db).id}", status_code=303)
    return render(
        request,
        "bookings/choose_service.html",
        current_user=user,
        mode=mode,
        services=[(s, ready_media(s.media)) for s in services],
        custom_service=engine.custom_request_service(db) if mode == "custom" else None,
    )


# --- Étape 2 : coiffeuse, jour, heure (public) ---------------------------------------------


@router.get("/new/{service_id}")
def pick_slot(
    request: Request,
    service_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    return _render_pick(request, db, user, _service_or_404(db, service_id))


@router.post("/edit")
def edit_booking(
    request: Request,
    service_id: int = Form(...),
    hairdresser: str = Form("any"),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    """« Modifier » depuis le récapitulatif : retour à l'étape 2 sans perdre la description ni les photos."""
    return _render_pick(
        request, db, user, _service_or_404(db, service_id), notes=notes.strip(),
        selected_hairdresser=hairdresser,
    )  # fmt: skip


@router.get("/calendar", dependencies=[Depends(_throttle_browsing)])
def calendar_fragment(
    request: Request,
    service_id: int,
    hairdresser: str = "any",
    month: str = "",
    cal_month: str = "",
    db: Session = Depends(get_db),
):
    service = _service_or_404(db, service_id)
    ctx = _calendar_context(db, service, _parse_choice(hairdresser), month or cal_month)
    return render(request, "bookings/_calendar.html", oob=True, **ctx)  # oob : remet la zone des heures à zéro


@router.get("/slots", dependencies=[Depends(_throttle_browsing)])
def slots_fragment(
    request: Request,
    service_id: int,
    hairdresser: str = "any",
    day: str = "",
    db: Session = Depends(get_db),
):
    service = _service_or_404(db, service_id)
    choice = _parse_choice(hairdresser)
    try:
        the_day = date.fromisoformat(day)
    except ValueError:
        return render(request, "bookings/_slots.html", slots=[], message="Choisissez un jour dans le calendrier.")
    slots = engine.slots_for(db, service, choice, the_day)
    quote_ = None
    if choice is not None:
        h = db.get(Hairdresser, choice)
        quote_ = engine.quote_for(db, service, h) if h and h.is_active else None
    return render(
        request,
        "bookings/_slots.html",
        slots=[start for start, _ in slots],
        quote=quote_,
        message=None if slots else "Aucun créneau disponible ce jour-là. Essayez une autre date ou une autre coiffeuse.",
    )


# --- Photos d'inspiration (mode sur mesure) : avant ou après la connexion --------------------


@router.post("/photos", dependencies=[Depends(limit_upload_size)])
def upload_photo(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    token, new_cookie = drafts.token_from(request), False
    if user:
        _claim(db, user, request)
        owner = drafts.Owner(user_id=user.id)
    else:
        if not token:
            token, new_cookie = drafts.new_token(), True
        owner = drafts.Owner(token_hash=drafts.hash_token(token))
    ip = request.client.host if request.client else "unknown"
    try:
        photo = photos.add_draft(db, owner, file, ip=ip)
    except PhotoRejected as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    response = JSONResponse(
        {"id": photo.id, "url": f"/bookings/photos/{photo.id}", "width": photo.width, "height": photo.height}
    )
    if new_cookie:
        drafts.set_cookie(response, token)
    return response


@router.get("/photos/{photo_id}")
def photo_file(
    request: Request,
    photo_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    photo = db.get(BookingPhoto, photo_id)
    token = drafts.token_from(request)
    # 404 (et non 403) : on ne révèle pas l'existence des photos d'autrui.
    if not photo or not photos.can_view(db, user, drafts.hash_token(token) if token else None, photo):
        raise HTTPException(status_code=404)
    return Response(
        get_private_storage().read(photo.storage_key),
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.post("/photos/{photo_id}/delete")
def photo_delete(
    request: Request,
    photo_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    photo = db.get(BookingPhoto, photo_id)
    if not photo or photo.appointment_id is not None or not photos.is_owner(photo, drafts.owner_for(user, request)):
        raise HTTPException(status_code=404)  # seules les photos non encore envoyées se retirent
    photos.delete_photo(db, photo)
    return {"status": "ok"}


# --- Étape 5 : récapitulatif (aucune écriture de rendez-vous) --------------------------------


def _recap_context(db: Session, service: Service, choice: int | None, start: datetime, notes: str):
    # Dans les deux cas (coiffeuse précise ou « peu importe »), le créneau doit être encore libre MAINTENANT :
    # entre le choix et la confirmation (surtout avec l'inscription au milieu), quelqu'un a pu le prendre.
    hairdresser = None
    day = start.astimezone(engine.salon_tz()).date()
    for s, h in engine.slots_for(db, service, choice, day):
        if s == start:
            hairdresser = h
            break
    if hairdresser is None:
        raise SlotUnavailable("Ce créneau n'est plus disponible. Merci d'en choisir un autre.")
    quote_ = engine.quote_for(db, service, hairdresser)
    return {
        "service": service,
        "mode": engine.booking_mode_for(service).value,
        "hairdresser": hairdresser,
        "choice": "any" if choice is None else str(choice),
        "quote": quote_,
        "start": start,
        "end": start + timedelta(minutes=quote_.duration_min),
        "notes": notes,
    }


def _remember_choice(db: Session, request: Request, response, service_id, hairdresser, start, notes, photo_ids):
    """Garde le choix d'une visiteuse non connectée pendant sa connexion (brouillon + cookie)."""
    token = drafts.token_from(request) or drafts.new_token()
    drafts.save_draft(
        db, token,
        {"service_id": service_id, "hairdresser": hairdresser, "start": start.isoformat(),
         "notes": notes, "photo_ids": list(photo_ids)},
    )  # fmt: skip
    drafts.set_cookie(response, token)


@router.post("/review")
def review(
    request: Request,
    service_id: int = Form(...),
    hairdresser: str = Form("any"),
    slot: str = Form(""),
    notes: str = Form(""),
    photo_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    service = _service_or_404(db, service_id)
    notes = notes.strip()
    mode = engine.booking_mode_for(service).value
    _claim(db, user, request)
    owner = drafts.owner_for(user, request)
    error, ctx, chosen = None, {}, []
    if not slot:
        error = "Choisissez un créneau."
    elif len(notes) > NOTES_MAX:
        error = f"La description est trop longue ({NOTES_MAX} caractères maximum)."
    elif mode == "custom" and len(notes) < CUSTOM_NOTES_MIN:
        error = "Décrivez votre demande (quelques mots au minimum) pour une réservation sur mesure."
    else:
        try:
            if mode == "custom":
                chosen = photos.load_for_booking(db, owner, photo_ids)
            ctx = _recap_context(db, service, _parse_choice(hairdresser), _parse_start(slot), notes)
        except (BookingError, PhotoRejected) as exc:
            error = str(exc)
    if error:
        return _render_pick(
            request, db, user, service, status_code=400, error=error, notes=notes, selected_hairdresser=hairdresser
        )
    response = render(
        request, "bookings/review.html", current_user=user, photos=chosen, anonymous=user is None,
        resume_url=RESUME_URL, **ctx,
    )  # fmt: skip
    if user is None:
        _remember_choice(db, request, response, service_id, hairdresser, ctx["start"], notes, [p.id for p in chosen])
    return response


@router.get("/resume")
def resume(request: Request, db: Session = Depends(get_db), user: User = Depends(require_login)):
    """Retour après connexion ou création de compte : le récapitulatif réapparaît, prêt à confirmer."""
    token = drafts.token_from(request)
    payload = drafts.load_draft(db, token)
    if not payload:
        return RedirectResponse("/bookings/new", status_code=303)
    service = _service_or_404(db, int(payload["service_id"]))
    drafts.claim_photos(db, user, token)
    start = datetime.fromisoformat(payload["start"]).astimezone(timezone.utc)
    notes = payload.get("notes", "")
    try:
        chosen = photos.load_for_booking(db, drafts.Owner(user_id=user.id), [int(i) for i in payload.get("photo_ids", [])])
        ctx = _recap_context(db, service, _parse_choice(payload.get("hairdresser", "any")), start, notes)
    except (BookingError, PhotoRejected) as exc:
        return _render_pick(
            request, db, user, service, status_code=409, error=str(exc), notes=notes,
            selected_hairdresser=payload.get("hairdresser", "any"),
        )  # fmt: skip
    return render(
        request, "bookings/review.html", current_user=user, photos=chosen, anonymous=False,
        resume_url=RESUME_URL, resumed=True, **ctx,
    )  # fmt: skip


# --- Étape 6 : confirmation (transaction atomique) -----------------------------------------


@router.post("")
def confirm(
    request: Request,
    service_id: int = Form(...),
    hairdresser: str = Form("any"),
    start: str = Form(...),
    notes: str = Form(""),
    photo_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    notes = notes.strip()
    if user is None:
        # Session expirée pendant le parcours : on garde le choix et on renvoie vers la connexion.
        response = RedirectResponse(f"/auth/login?next={quote(RESUME_URL)}", status_code=303)
        _remember_choice(db, request, response, service_id, hairdresser, _parse_start(start), notes, photo_ids)
        return response
    service = _service_or_404(db, service_id)
    _claim(db, user, request)
    if engine.booking_mode_for(service).value == "custom" and len(notes) < CUSTOM_NOTES_MIN:
        raise HTTPException(status_code=400, detail="Description requise")
    try:
        appt = engine.create_booking(
            db, client=user, service=service, hairdresser_id=_parse_choice(hairdresser),
            start=_parse_start(start), notes=notes, photo_ids=photo_ids,
        )  # fmt: skip
    except BookingError as exc:
        return _render_pick(
            request, db, user, service, status_code=409 if isinstance(exc, SlotUnavailable) else 400,
            error=str(exc), notes=notes, selected_hairdresser=hairdresser,
        )  # fmt: skip
    response = RedirectResponse(f"/bookings/{appt.id}?new=1", status_code=303)
    drafts.delete_draft(db, drafts.token_from(request))  # le brouillon a servi
    drafts.clear_cookie(response)
    return response


# --- Mes rendez-vous ---------------------------------------------------------------------------


def _load_owned(db: Session, appointment_id: int, user: User) -> Appointment:
    appt = db.scalar(
        select(Appointment)
        .options(
            selectinload(Appointment.service),
            selectinload(Appointment.photos),
            selectinload(Appointment.hairdresser).selectinload(Hairdresser.user),
        )
        .where(Appointment.id == appointment_id)
    )
    # 404 (et non 403) pour ne pas révéler l'existence des rendez-vous d'autrui.
    if not appt or (appt.client_id != user.id and user.role != Role.admin):
        raise HTTPException(status_code=404)
    return appt


@router.get("")
def my_bookings(request: Request, db: Session = Depends(get_db), user: User = Depends(require_login)):
    now = engine.utcnow()
    rows = db.scalars(
        select(Appointment)
        .options(selectinload(Appointment.service), selectinload(Appointment.hairdresser).selectinload(Hairdresser.user))
        .where(Appointment.client_id == user.id)
        .order_by(Appointment.starts_at)
    ).all()
    upcoming = [a for a in rows if a.status == AppointmentStatus.confirmed and a.starts_at > now]
    past = [a for a in reversed(rows) if a not in upcoming]
    return render(request, "bookings/mine.html", current_user=user, upcoming=upcoming, past=past, now=now)


@router.get("/{appointment_id}")
def booking_detail(
    request: Request, appointment_id: int, db: Session = Depends(get_db), user: User = Depends(require_login)
):
    appt = _load_owned(db, appointment_id, user)
    return render(
        request, "bookings/detail.html", current_user=user, appt=appt,
        is_new=request.query_params.get("new") == "1", now=engine.utcnow(),
    )  # fmt: skip


@router.post("/{appointment_id}/cancel")
def cancel(appointment_id: int, db: Session = Depends(get_db), user: User = Depends(require_login)):
    appt = _load_owned(db, appointment_id, user)
    try:
        engine.cancel_booking(db, appt)
    except BookingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return RedirectResponse(f"/bookings/{appt.id}?cancelled=1", status_code=303)
