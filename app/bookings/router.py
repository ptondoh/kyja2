import calendar
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import get_current_user_optional, require_login, require_user
from app.bookings import engine, photos
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


async def limit_upload_size(request: Request) -> None:
    """Refuse tôt les envois trop gros (Nginx reste le vrai garde-fou)."""
    cap = (get_settings().image_max_mb + 1) * 2**20
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise HTTPException(status_code=413, detail="Photo trop volumineuse")


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


def _render_pick(request, db, user, service, status_code=200, **extra):
    """Étape 2 : coiffeuse, date, créneau (+ description et photos en mode sur mesure)."""
    mode = engine.booking_mode_for(service).value
    selected = extra.get("selected_hairdresser") or "any"
    choice = int(selected) if str(selected).isdigit() else None
    return render(
        request, "bookings/pick.html", status_code=status_code, current_user=user, service=service, mode=mode,
        choices=_choices(db, service), oob=False, **_calendar_context(db, service, choice),
        drafts=photos.drafts(db, user.id) if mode == "custom" else [], max_photos=MAX_PHOTOS, **extra,
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


# --- Étape 3 : coiffeuse, date, créneau ---------------------------------------------------


@router.get("/new/{service_id}")
def pick_slot(
    request: Request,
    service_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    return _render_pick(request, db, user, _service_or_404(db, service_id))


@router.post("/edit")
def edit_booking(
    request: Request,
    service_id: int = Form(...),
    hairdresser: str = Form("any"),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """« Modifier » depuis le récapitulatif : retour à l'étape 2 sans perdre la description ni les photos."""
    return _render_pick(
        request, db, user, _service_or_404(db, service_id), notes=notes.strip(),
        selected_hairdresser=hairdresser,
    )  # fmt: skip


@router.get("/calendar")
def calendar_fragment(
    request: Request,
    service_id: int,
    hairdresser: str = "any",
    month: str = "",
    cal_month: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    service = _service_or_404(db, service_id)
    ctx = _calendar_context(db, service, _parse_choice(hairdresser), month or cal_month)
    return render(request, "bookings/_calendar.html", oob=True, **ctx)  # oob : remet la zone des heures à zéro


@router.get("/slots")
def slots_fragment(
    request: Request,
    service_id: int,
    hairdresser: str = "any",
    day: str = "",
    db: Session = Depends(get_db),
    user: User = Depends(require_user),
):
    service = _service_or_404(db, service_id)
    choice = _parse_choice(hairdresser)
    try:
        the_day = date.fromisoformat(day)
    except ValueError:
        return render(request, "bookings/_slots.html", slots=[], message="Choisissez un jour dans le calendrier.")
    slots = engine.slots_for(db, service, choice, the_day)
    quote = None
    if choice is not None:
        h = db.get(Hairdresser, choice)
        quote = engine.quote_for(db, service, h) if h and h.is_active else None
    return render(
        request,
        "bookings/_slots.html",
        slots=[start for start, _ in slots],
        quote=quote,
        message=None if slots else "Aucun créneau disponible ce jour-là. Essayez une autre date ou une autre coiffeuse.",
    )


# --- Photos d'inspiration (mode sur mesure) -------------------------------------------------


@router.post("/photos", dependencies=[Depends(limit_upload_size)])
def upload_photo(
    file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_user)
):
    try:
        photo = photos.add_draft(db, user, file)
    except PhotoRejected as exc:
        return JSONResponse({"detail": str(exc)}, status_code=400)
    return {"id": photo.id, "url": f"/bookings/photos/{photo.id}", "width": photo.width, "height": photo.height}


@router.get("/photos/{photo_id}")
def photo_file(photo_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    photo = db.get(BookingPhoto, photo_id)
    # 404 (et non 403) : on ne révèle pas l'existence des photos d'autrui.
    if not photo or not photos.can_view(db, user, photo):
        raise HTTPException(status_code=404)
    return Response(
        get_private_storage().read(photo.storage_key),
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=3600"},
    )


@router.post("/photos/{photo_id}/delete")
def photo_delete(photo_id: int, db: Session = Depends(get_db), user: User = Depends(require_user)):
    photo = db.get(BookingPhoto, photo_id)
    if not photo or photo.client_id != user.id or photo.appointment_id is not None:
        raise HTTPException(status_code=404)  # seules les photos non encore envoyées se retirent
    photos.delete_photo(db, photo)
    return {"status": "ok"}


# --- Étape 5 : récapitulatif (aucune écriture) --------------------------------------------


def _recap_context(db: Session, service: Service, choice: int | None, start: datetime, notes: str):
    hairdresser = None
    if choice is not None:
        hairdresser = db.get(Hairdresser, choice)
    else:  # « peu importe » : première coiffeuse qui peut prendre ce créneau
        day = start.astimezone(engine.salon_tz()).date()
        for s, h in engine.slots_for(db, service, None, day):
            if s == start:
                hairdresser = h
                break
    if hairdresser is None:
        raise SlotUnavailable("Ce créneau n'est plus disponible.")
    quote = engine.quote_for(db, service, hairdresser)
    return {
        "service": service,
        "mode": engine.booking_mode_for(service).value,
        "hairdresser": hairdresser,
        "choice": "any" if choice is None else str(choice),
        "quote": quote,
        "start": start,
        "end": start + timedelta(minutes=quote.duration_min),
        "notes": notes,
    }


@router.post("/review")
def review(
    request: Request,
    service_id: int = Form(...),
    hairdresser: str = Form("any"),
    slot: str = Form(""),
    notes: str = Form(""),
    photo_ids: list[int] = Form(default=[]),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    service = _service_or_404(db, service_id)
    notes = notes.strip()
    mode = engine.booking_mode_for(service).value
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
                chosen = photos.load_for_booking(db, user, photo_ids)
            ctx = _recap_context(db, service, _parse_choice(hairdresser), _parse_start(slot), notes)
        except (BookingError, PhotoRejected) as exc:
            error = str(exc)
    if error:
        return _render_pick(
            request, db, user, service, status_code=400, error=error, notes=notes, selected_hairdresser=hairdresser
        )
    return render(request, "bookings/review.html", current_user=user, photos=chosen, **ctx)


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
    user: User = Depends(require_login),
):
    service = _service_or_404(db, service_id)
    notes = notes.strip()
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
    return RedirectResponse(f"/bookings/{appt.id}?new=1", status_code=303)


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
