"""Rappels de rendez-vous : la veille (dans les 24 h) et environ 2 h avant, une seule fois chacun.

Appelé toutes les 10 minutes par Celery (ou à la main). Un rendez-vous pris trop tard pour un rappel
(par exemple réservé le matin même) est marqué « rappel passé » sans rien envoyer.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.db.models.core import Appointment, AppointmentStatus, Hairdresser
from app.notifications import messages
from app.notifications import service as notifications
from app.tasks import dispatch

DAY_BEFORE = timedelta(hours=24)
HOURS_BEFORE = timedelta(hours=2)


def _claim(db: Session, appt_id: int, column, now: datetime) -> bool:
    """Réserve l'envoi : un seul processus peut marquer ce rappel (deux « beat » ne l'enverront pas deux fois)."""
    claimed = db.execute(
        update(Appointment).where(Appointment.id == appt_id, column.is_(None)).values({column.key: now})
    ).rowcount
    db.commit()
    return bool(claimed)


def send_due_reminders(db: Session, now: datetime | None = None, only_appointment_id: int | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    sent = {"day": 0, "hours": 0}
    rows = db.scalars(
        select(Appointment)
        .options(selectinload(Appointment.client), selectinload(Appointment.service), selectinload(Appointment.hairdresser).selectinload(Hairdresser.user))
        .where(
            Appointment.status == AppointmentStatus.confirmed,
            Appointment.starts_at > now,
            Appointment.starts_at <= now + DAY_BEFORE,
            (Appointment.reminder_day_sent_at.is_(None)) | (Appointment.reminder_hours_sent_at.is_(None)),
        )
        .order_by(Appointment.starts_at)
    ).all()
    for appt in rows:
        if only_appointment_id is not None and appt.id != only_appointment_id:  # essai en direct : un seul rendez-vous
            continue
        until = appt.starts_at - now
        booked_late_for = lambda lead: appt.created_at > appt.starts_at - lead  # noqa: E731
        if until > HOURS_BEFORE:
            if appt.reminder_day_sent_at is None and _claim(db, appt.id, Appointment.reminder_day_sent_at, now):
                if not booked_late_for(DAY_BEFORE):
                    _send(db, appt, days_before=True)
                    sent["day"] += 1
        else:
            if appt.reminder_day_sent_at is None:  # la fenêtre de la veille est passée sans envoi : on ne la rattrape pas
                _claim(db, appt.id, Appointment.reminder_day_sent_at, now)
            if appt.reminder_hours_sent_at is None and _claim(db, appt.id, Appointment.reminder_hours_sent_at, now):
                if not booked_late_for(HOURS_BEFORE):
                    _send(db, appt, days_before=False)
                    sent["hours"] += 1
    return sent


def _send(db: Session, appt: Appointment, *, days_before: bool) -> None:
    text = messages.reminder(appt, days_before)
    ids = notifications.notify_user(
        db, appt.client, kind="reminder", title=text["title"], body=text["body"], url=f"/bookings/{appt.id}", appointment=appt,
        email_subject=text["email_subject"], email_body=text["email_body"], sms_body=text["sms_body"],
    )  # fmt: skip
    dispatch.send_messages(ids)
