"""Agenda de la coiffeuse : tous les rendez-vous pris avec elle (confirmés ou non), séparés de sa vue cliente."""
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import require_role
from app.bookings import engine, quotes
from app.bookings.quotes import QuoteError
from app.db.base import get_db
from app.db.models.core import Appointment, AppointmentStatus, BookingMode, Hairdresser, Role, User
from app.notifications import service as notifications
from app.security_middleware import verify_csrf
from app.templating import render

router = APIRouter(prefix="/agenda", dependencies=[Depends(verify_csrf)])

TYPES = {"all": "Tous", "custom": "Demandes sur mesure", "catalog": "Coiffures du catalogue"}


def _own_hairdresser(db: Session, user: User) -> Hairdresser:
    h = db.scalar(select(Hairdresser).where(Hairdresser.user_id == user.id))
    if h is None:
        raise HTTPException(status_code=404)
    return h


def _load(db: Session, h: Hairdresser, appointment_id: int) -> Appointment:
    appt = db.scalar(
        select(Appointment)
        .options(selectinload(Appointment.client), selectinload(Appointment.service), selectinload(Appointment.photos))
        .where(Appointment.id == appointment_id, Appointment.hairdresser_id == h.id)
    )
    if appt is None:  # 404 : on ne révèle pas les rendez-vous des autres coiffeuses
        raise HTTPException(status_code=404)
    return appt


@router.get("")
def agenda(
    request: Request, type: str = "all", db: Session = Depends(get_db), user: User = Depends(require_role(Role.hairdresser))
):
    h = _own_hairdresser(db, user)
    if type not in TYPES:
        type = "all"
    q = (
        select(Appointment)
        .options(selectinload(Appointment.client), selectinload(Appointment.service))
        .where(Appointment.hairdresser_id == h.id)
        .order_by(Appointment.starts_at)
    )
    if type != "all":
        q = q.where(Appointment.booking_mode == BookingMode(type))
    rows = db.scalars(q).all()
    now = engine.utcnow()
    horizon = now - timedelta(days=60)  # l'historique plus ancien reste en base, mais n'encombre pas l'écran

    def upcoming(status):
        return [a for a in rows if a.status == status and a.starts_at > now]

    past = [a for a in reversed(rows) if a.starts_at > horizon and (a.starts_at <= now or a.status == AppointmentStatus.cancelled)]
    groups = [
        ("Prix à proposer", "Demandes sur mesure : regardez les photos puis proposez un prix.", upcoming(AppointmentStatus.pending_quote)),
        ("En attente de la cliente", "Prix proposé : la cliente doit l'accepter ou le refuser.", upcoming(AppointmentStatus.awaiting_client)),
        ("Confirmés à venir", "", upcoming(AppointmentStatus.confirmed)),
        ("Passés et annulés", "", past),
    ]
    return render(request, "hairdressers/agenda.html", current_user=user, groups=groups, types=TYPES, type=type, now=now)


@router.get("/{appointment_id}")
def agenda_detail(request: Request, appointment_id: int, db: Session = Depends(get_db), user: User = Depends(require_role(Role.hairdresser))):
    appt = _load(db, _own_hairdresser(db, user), appointment_id)
    return render(request, "hairdressers/appointment.html", current_user=user, appt=appt, now=engine.utcnow(), sent=request.query_params.get("sent"), error=None)


@router.post("/{appointment_id}/quote")
def agenda_quote(
    request: Request,
    appointment_id: int,
    background: BackgroundTasks,
    price: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.hairdresser)),
):
    appt = _load(db, _own_hairdresser(db, user), appointment_id)
    try:
        ids = quotes.propose_and_notify(db, appt, quotes.parse_price(price), user)
    except QuoteError as exc:
        return render(request, "hairdressers/appointment.html", status_code=400, current_user=user, appt=appt, now=engine.utcnow(), sent=None, error=str(exc))
    background.add_task(notifications.deliver_in_background, ids)
    return RedirectResponse(f"/agenda/{appt.id}?sent=1", status_code=303)
