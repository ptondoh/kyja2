"""Administration de la réservation : coiffeuses, horaires, équipements, rendez-vous, prix final."""
import re
import secrets
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
import unicodedata

from sqlalchemy import delete, false, or_, select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import require_role
from app.auth.security import hash_password
from app.bookings import engine, quotes
from app.bookings.engine import BookingError
from app.db.base import get_db
from app.filters import matching, plain as _plain
from app.pagination import like, paginate
from app.db.models.core import (
    Appointment,
    AppointmentStatus,
    BookingMode,
    MessageOutbox,
    AvailabilitySlot,
    Hairdresser,
    Resource,
    Role,
    Service,
    User,
)
from app.notifications import service as notifications
from app.security_middleware import verify_csrf
from app.services import lifecycle
from app.templating import STATUS_LABELS, render

router = APIRouter(
    prefix="/admin", dependencies=[Depends(require_role(Role.admin)), Depends(verify_csrf)]
)

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
KIND_RE = re.compile(r"^[a-z0-9_]{1,50}$")
MIN_PASSWORD = 12
TYPE_LABELS = {"custom": "Sur mesure", "catalog": "Catalogue"}
WHEN_LABELS = {"avenir": "À venir", "passes": "Passés"}


WEEKDAYS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _coefficient(raw: str, errors: list[str]) -> Decimal | None:
    try:
        v = Decimal(raw.replace(",", ".").strip())
    except (InvalidOperation, AttributeError):
        errors.append("Coefficient invalide.")
        return None
    if not (Decimal("0.50") <= v <= Decimal("3.00")) or v != v.quantize(Decimal("0.01")):
        errors.append("Le coefficient doit être entre 0,50 et 3,00 (2 décimales).")
        return None
    return v


def _minutes(raw: str) -> int | None:
    m = TIME_RE.match(raw.strip())
    return int(m.group(1)) * 60 + int(m.group(2)) if m else None


# --- Coiffeuses -----------------------------------------------------------------------------


@router.get("/hairdressers")
def hairdressers_list(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))
):
    archived = request.query_params.get("archived") == "1"
    q = (request.query_params.get("q") or "").strip()
    query = (
        select(Hairdresser)
        .join(User, Hairdresser.user_id == User.id)
        .options(selectinload(Hairdresser.user), selectinload(Hairdresser.slots))
        .where(Hairdresser.archived_at.is_not(None) if archived else Hairdresser.archived_at.is_(None))
        .order_by(Hairdresser.id)
    )
    if q:
        query = query.where(or_(User.full_name.ilike(like(q), escape="\\"), User.email.ilike(like(q), escape="\\")))
    pg = paginate(db, query, request.query_params.get("page"))
    return render(request, "admin/hairdressers.html", current_user=user, items=pg.items, pg=pg, q=q, show_archived=archived)


@router.post("/hairdressers")
def hairdresser_create(
    request: Request,
    full_name: str = Form(""),
    email: str = Form(""),
    password: str = Form(""),
    coefficient: str = Form("1.00"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.admin)),
):
    errors: list[str] = []
    email = email.strip().lower()
    full_name = full_name.strip()
    if not full_name or len(full_name) > 200:
        errors.append("Le nom est requis.")
    if not EMAIL_RE.match(email):
        errors.append("Adresse email invalide.")
    elif db.scalar(select(User.id).where(User.email == email)):
        errors.append("Un compte existe déjà avec cet email.")
    if not (MIN_PASSWORD <= len(password) <= 128):
        errors.append(f"Le mot de passe provisoire doit contenir au moins {MIN_PASSWORD} caractères.")
    coeff = _coefficient(coefficient, errors)
    if errors:
        pg = paginate(
            db,
            select(Hairdresser)
            .options(selectinload(Hairdresser.user), selectinload(Hairdresser.slots))
            .where(Hairdresser.archived_at.is_(None))
            .order_by(Hairdresser.id),
            1,
        )
        return render(
            request, "admin/hairdressers.html", status_code=400, current_user=user, items=pg.items, pg=pg, q="",
            errors=errors, form={"full_name": full_name, "email": email, "coefficient": coefficient},
        )  # fmt: skip
    account = User(email=email, password_hash=hash_password(password), role=Role.hairdresser, full_name=full_name)
    db.add(account)
    db.flush()
    h = Hairdresser(user_id=account.id, duration_coefficient=coeff)
    db.add(h)
    db.commit()
    return _redirect(f"/admin/hairdressers/{h.id}?saved=1")


def _hairdresser_or_404(db: Session, hairdresser_id: int) -> Hairdresser:
    h = db.scalar(
        select(Hairdresser)
        .options(selectinload(Hairdresser.user), selectinload(Hairdresser.slots))
        .where(Hairdresser.id == hairdresser_id)
    )
    if not h:
        raise HTTPException(status_code=404)
    return h


@router.get("/hairdressers/{hairdresser_id}")
def hairdresser_edit(
    request: Request,
    hairdresser_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.admin)),
):
    h = _hairdresser_or_404(db, hairdresser_id)
    slots = sorted(h.slots, key=lambda s: (s.weekday, s.start_minute))
    return render(request, "admin/hairdresser_form.html", current_user=user, item=h, slots=slots, weekday_names=WEEKDAYS)


@router.post("/hairdressers/{hairdresser_id}")
def hairdresser_update(
    request: Request,
    hairdresser_id: int,
    coefficient: str = Form(...),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.admin)),
):
    h = _hairdresser_or_404(db, hairdresser_id)
    errors: list[str] = []
    coeff = _coefficient(coefficient, errors)
    if errors:
        slots = sorted(h.slots, key=lambda s: (s.weekday, s.start_minute))
        return render(
            request, "admin/hairdresser_form.html", status_code=400, current_user=user, item=h,
            slots=slots, weekday_names=WEEKDAYS, errors=errors,
        )  # fmt: skip
    h.duration_coefficient = coeff
    h.is_active = is_active
    db.commit()  # n'affecte pas les rendez-vous déjà pris : le calcul est mémorisé dans chacun
    return _redirect(f"/admin/hairdressers/{h.id}?saved=1")


@router.post("/hairdressers/{hairdresser_id}/slots")
def slot_add(
    request: Request,
    hairdresser_id: int,
    weekday: int = Form(...),
    start: str = Form(...),
    end: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.admin)),
):
    h = _hairdresser_or_404(db, hairdresser_id)
    s_min, e_min = _minutes(start), _minutes(end)
    error = None
    if not (0 <= weekday <= 6) or s_min is None or e_min is None:
        error = "Jour ou heure invalide (format HH:MM)."
    elif s_min >= e_min:
        error = "L'heure de fin doit être après l'heure de début."
    elif any(s.weekday == weekday and s.start_minute < e_min and s.end_minute > s_min for s in h.slots):
        error = "Cette plage chevauche une plage existante ce jour-là."
    if error:
        slots = sorted(h.slots, key=lambda s: (s.weekday, s.start_minute))
        return render(
            request, "admin/hairdresser_form.html", status_code=400, current_user=user, item=h,
            slots=slots, weekday_names=WEEKDAYS, errors=[error],
        )  # fmt: skip
    db.add(AvailabilitySlot(hairdresser_id=h.id, weekday=weekday, start_minute=s_min, end_minute=e_min))
    db.commit()
    return _redirect(f"/admin/hairdressers/{h.id}?saved=1")


@router.post("/slots/{slot_id}/delete")
def slot_delete(slot_id: int, db: Session = Depends(get_db)):
    slot = db.get(AvailabilitySlot, slot_id)
    if not slot:
        raise HTTPException(status_code=404)
    back = f"/admin/hairdressers/{slot.hairdresser_id}"
    db.delete(slot)
    db.commit()
    return _redirect(back)


# --- Équipements ------------------------------------------------------------------------------


@router.get("/resources")
def resources_list(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))
):
    items = db.scalars(select(Resource).order_by(Resource.kind, Resource.id)).all()
    return render(request, "admin/resources.html", current_user=user, items=items)


@router.post("/resources")
def resource_create(
    request: Request,
    kind: str = Form(""),
    name: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.admin)),
):
    kind, name = kind.strip().lower(), name.strip()
    errors = []
    if not KIND_RE.match(kind):
        errors.append("Type : lettres minuscules, chiffres et _ seulement (ex: wash_basin).")
    if not name or len(name) > 100:
        errors.append("Le nom est requis (100 caractères max).")
    if errors:
        items = db.scalars(select(Resource).order_by(Resource.kind, Resource.id)).all()
        return render(request, "admin/resources.html", status_code=400, current_user=user, items=items, errors=errors)
    db.add(Resource(kind=kind, name=name))
    db.commit()
    return _redirect("/admin/resources")


@router.post("/resources/{resource_id}/toggle")
def resource_toggle(resource_id: int, db: Session = Depends(get_db)):
    r = db.get(Resource, resource_id)
    if not r:
        raise HTTPException(status_code=404)
    r.is_active = not r.is_active
    db.commit()
    return _redirect("/admin/resources")


# --- Rendez-vous ---------------------------------------------------------------------------------


@router.get("/appointments")
def appointments_list(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))
):
    params = request.query_params
    now = engine.utcnow()
    f = {k: (params.get(k) or "").strip() for k in ("statut", "type", "coiffeuse", "quand", "q")}
    query = (
        select(Appointment)
        .options(
            selectinload(Appointment.client),
            selectinload(Appointment.service),
            selectinload(Appointment.photos),
            selectinload(Appointment.hairdresser).selectinload(Hairdresser.user),
        )
        .order_by(Appointment.starts_at.desc())
    )
    # Chaque filtre est une zone de saisie (avec liste déroulante) : on tape quelques lettres ou on choisit une valeur.
    status_labels = {s.value: STATUS_LABELS.get(s.value, s.value) for s in AppointmentStatus}
    by_status = matching(f["statut"], status_labels)
    if by_status is not None:
        query = query.where(Appointment.status.in_([AppointmentStatus(v) for v in by_status]))
    by_type = matching(f["type"], TYPE_LABELS)
    if by_type is not None:
        query = query.where(Appointment.booking_mode.in_([BookingMode(v) for v in by_type]))
    if f["coiffeuse"]:
        query = query.where(Appointment.hairdresser_id.in_(
            select(Hairdresser.id).join(User, Hairdresser.user_id == User.id).where(User.full_name.ilike(like(f["coiffeuse"]), escape="\\"))
        ))  # fmt: skip
    by_when = matching(f["quand"], WHEN_LABELS)
    if by_when is not None:
        if by_when == ["avenir"]:
            query = query.where(Appointment.starts_at > now)
        elif by_when == ["passes"]:
            query = query.where(Appointment.starts_at <= now)
        elif not by_when:
            query = query.where(false())
    if f["q"]:
        query = query.join(User, Appointment.client_id == User.id).where(or_(User.full_name.ilike(like(f["q"]), escape="\\"), User.email.ilike(like(f["q"]), escape="\\")))
    pg = paginate(db, query, params.get("page"))
    hairdressers = db.scalars(select(Hairdresser).options(selectinload(Hairdresser.user)).order_by(Hairdresser.id)).all()
    return render(
        request, "admin/appointments.html", current_user=user, items=pg.items, pg=pg, f=f, now=now,
        statuses=list(status_labels.values()), types=list(TYPE_LABELS.values()), whens=list(WHEN_LABELS.values()),
        hairdressers=[h.user.full_name for h in hairdressers],
    )  # fmt: skip


@router.get("/messages")
def messages_log(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    """Journal des courriels et SMS. « aperçu » = aucun fournisseur configuré, rien n'est parti."""
    params = request.query_params
    f = {"canal": (params.get("canal") or "").strip(), "statut": (params.get("statut") or "").strip()}
    query = select(MessageOutbox).order_by(MessageOutbox.id.desc())
    by_channel = matching(f["canal"], {"email": "Courriel", "sms": "SMS"})
    if by_channel is not None:
        query = query.where(MessageOutbox.channel.in_(by_channel))
    by_status = matching(f["statut"], {"preview": "Aperçu", "sent": "Envoyé", "failed": "Échec", "queued": "En file"})
    if by_status is not None:
        query = query.where(MessageOutbox.status.in_(by_status))
    pg = paginate(db, query, params.get("page"), per_page=25)
    rows = pg.items
    from app.notifications import providers

    return render(
        request, "admin/messages.html", current_user=user, rows=rows, pg=pg, f=f,
        email_ready=providers.email_configured(), sms_ready=providers.sms_configured(),
    )  # fmt: skip


@router.post("/messages/{message_id}/retry")
def messages_retry(message_id: int, background: BackgroundTasks, db: Session = Depends(get_db)):
    row = db.get(MessageOutbox, message_id)
    if not row or row.status not in ("failed", "queued"):
        raise HTTPException(status_code=404)
    row.attempts = 0
    row.status = "queued"
    db.commit()
    background.add_task(notifications.deliver_in_background, [row.id])
    return _redirect("/admin/messages")


@router.post("/appointments/{appointment_id}/final-price")
def appointment_final_price(
    appointment_id: int,
    background: BackgroundTasks,
    price: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.admin)),
):
    """Demande sur mesure en attente : PROPOSE le prix (la cliente est avertie et doit l'accepter).
    Autres rendez-vous : enregistre le prix final payé au salon."""
    appt = db.get(Appointment, appointment_id)
    if not appt:
        raise HTTPException(status_code=404)
    try:
        value = quotes.parse_price(price)
        if appt.status in (AppointmentStatus.pending_quote, AppointmentStatus.awaiting_client):
            ids = quotes.propose_and_notify(db, appt, value, user)
            background.add_task(notifications.deliver_in_background, ids)
            return _redirect("/admin/appointments?quoted=1")
        if value != value.quantize(Decimal("0.01")) or not (Decimal("0") <= value <= Decimal("99999.99")):
            raise BookingError("Prix invalide (2 décimales max).")
        engine.set_final_price(db, appt, value)
    except BookingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _redirect("/admin/appointments")


@router.post("/appointments/{appointment_id}/cancel")
def appointment_cancel(appointment_id: int, db: Session = Depends(get_db)):
    appt = db.get(Appointment, appointment_id)
    if not appt:
        raise HTTPException(status_code=404)
    try:
        engine.cancel_booking(db, appt, reason="admin")
    except BookingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _redirect("/admin/appointments")


# --- Rendez-vous saisi par le salon (cliente qui réserve par téléphone) ----------------------------------


def _new_appointment_context(db: Session, **extra) -> dict:
    services = db.scalars(select(Service).where(Service.is_active.is_(True)).order_by(Service.is_custom_request, Service.name)).all()
    return {"services": services, "hairdressers": engine.active_hairdressers(db), **extra}


@router.get("/appointments/new")
def appointment_new_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))
):
    return render(request, "admin/appointment_new.html", current_user=user, **_new_appointment_context(db))


@router.post("/appointments/new")
def appointment_new(
    request: Request,
    full_name: str = Form(""),
    phone: str = Form(""),
    email: str = Form(""),
    service_id: int = Form(...),
    hairdresser: str = Form("any"),
    day: str = Form(""),
    time: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.admin)),
):
    form = {"full_name": full_name, "phone": phone, "email": email, "service_id": service_id,
            "hairdresser": hairdresser, "day": day, "time": time, "notes": notes}  # fmt: skip

    def fail(*messages: str):
        return render(
            request, "admin/appointment_new.html", status_code=400, current_user=user,
            errors=list(messages), form=form, **_new_appointment_context(db),
        )  # fmt: skip

    full_name, phone, email = full_name.strip(), phone.strip(), email.strip().lower()
    service = db.get(Service, service_id)
    if not service or not service.is_active:
        return fail("Prestation invalide.")
    if email and not EMAIL_RE.match(email):
        return fail("Adresse email invalide.")
    existing = db.scalar(select(User).where(User.email == email)) if email else None
    if not existing and (not full_name or len(full_name) > 200):
        return fail("Le nom de la cliente est requis.")
    if not existing and not (phone or email):
        return fail("Indiquez au moins un téléphone ou un email pour pouvoir joindre la cliente.")
    if phone and not re.fullmatch(r"[0-9 ()+.\-]{5,30}", phone):
        return fail("Téléphone invalide.")
    try:
        the_day = date.fromisoformat(day)
    except ValueError:
        return fail("Date invalide.")
    minutes = _minutes(time)
    if minutes is None:
        return fail("Heure invalide (format HH:MM).")
    start = datetime(the_day.year, the_day.month, the_day.day, minutes // 60, minutes % 60,
                     tzinfo=engine.salon_tz()).astimezone(timezone.utc)  # fmt: skip
    choice = None if hairdresser == "any" else (int(hairdresser) if hairdresser.isdigit() else -1)
    if choice == -1:
        return fail("Coiffeuse invalide.")

    client = existing
    if client is None:
        # Cliente sans compte (réservation par téléphone) : fiche créée par le salon, sans mot de passe utilisable.
        client = User(
            email=email or f"tel-{secrets.token_hex(8)}@sans-courriel.kyja",
            password_hash=hash_password(secrets.token_urlsafe(32)),
            role=Role.client,
            full_name=full_name,
            phone=phone or None,
        )
        db.add(client)
        db.flush()
    try:
        appt = engine.create_booking(
            db, client=client, service=service, hairdresser_id=choice, start=start,
            notes=notes.strip()[:2000] or None, admin_override=True,
        )  # fmt: skip
    except BookingError as exc:
        # create_booking valide la transaction avant de poser ses verrous : la fiche créée plus haut a donc
        # déjà été enregistrée. On la retire pour ne pas laisser de cliente orpheline.
        db.rollback()
        if existing is None:
            db.execute(delete(User).where(User.id == client.id))
            db.commit()
        return fail(str(exc))
    return _redirect(f"/admin/appointments?created={appt.id}")


# --- « Supprimer » une coiffeuse = l'archiver : historique et coordonnées conservés ----------------------------------


@router.get("/hairdressers/{hairdresser_id}/delete")
def hairdresser_delete_page(
    request: Request, hairdresser_id: int, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))
):
    h = _hairdresser_or_404(db, hairdresser_id)
    return render(
        request, "admin/hairdresser_delete.html", current_user=user, item=h,
        future=lifecycle.future_count(db, hairdresser_id=h.id), past=lifecycle.has_history(db, hairdresser_id=h.id),
    )  # fmt: skip


@router.post("/hairdressers/{hairdresser_id}/delete")
def hairdresser_delete(
    request: Request,
    hairdresser_id: int,
    cancel_future: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.admin)),
):
    h = _hairdresser_or_404(db, hairdresser_id)
    try:
        cancelled = lifecycle.archive_hairdresser(db, h, cancel_future=cancel_future)
    except lifecycle.InUse as exc:
        n = exc.blockers[0][1]
        return render(
            request, "admin/hairdresser_delete.html", status_code=400, current_user=user, item=h, future=n,
            past=lifecycle.has_history(db, hairdresser_id=h.id),
            errors=[f"{n} rendez-vous à venir sont prévus avec elle. Cochez la case pour les annuler, ou annulez-les d'abord."],
        )  # fmt: skip
    return _redirect(f"/admin/hairdressers?removed={quote(h.user.full_name)}&cancelled={cancelled}")


@router.post("/hairdressers/{hairdresser_id}/restore")
def hairdresser_restore(hairdresser_id: int, db: Session = Depends(get_db)):
    lifecycle.restore_hairdresser(db, _hairdresser_or_404(db, hairdresser_id))
    return _redirect(f"/admin/hairdressers/{hairdresser_id}?saved=1")
