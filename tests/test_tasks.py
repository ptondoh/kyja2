"""Tâches de fond : rappels de rendez-vous, envoi par Celery (sur place en test), reprise et planification."""
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, update

from app.bookings import engine
from app.db.base import SessionLocal
from app.db.models.core import Appointment, AppointmentStatus, MessageOutbox, Notification, Service, User
from app.notifications import providers, reminders
from app.notifications import service as notifications
from app.tasks import dispatch, jobs
from app.tasks.celery_app import celery_app
from tests.conftest import local_start, make_hairdresser, make_service, next_weekday, register
from tests.test_custom_quotes import visitor


@pytest.fixture
def booking():
    """Une cliente, un rendez-vous confirmé lundi 10 h, créé « il y a trois jours »."""
    with SessionLocal() as db:
        marie = make_hairdresser(db, "Marie", "1.00")
        svc = make_service(db, "TEST Brushing", base=60, price="55.00")
        ids = (marie.id, svc.id)
    c = visitor()
    register(c, email="cliente@test.kyja")
    with SessionLocal() as db:
        client = db.scalar(select(User).where(User.email == "cliente@test.kyja"))
        appt = engine.create_booking(
            db, client=client, service=db.get(Service, ids[1]), hairdresser_id=ids[0], start=local_start(next_weekday(0), 10)
        )
        db.execute(update(Appointment).where(Appointment.id == appt.id).values(created_at=appt.starts_at - timedelta(days=3)))
        db.commit()
        return {"id": appt.id, "start": appt.starts_at, "client": client.id}


def reminders_at(now):
    with SessionLocal() as db:
        return reminders.send_due_reminders(db, now)


def titles(client_id):
    with SessionLocal() as db:
        return db.scalars(select(Notification.title).where(Notification.user_id == client_id, Notification.kind == "reminder")).all()


def test_day_before_reminder_is_sent_once(booking):
    assert reminders_at(booking["start"] - timedelta(hours=30)) == {"day": 0, "hours": 0}  # trop tôt
    assert reminders_at(booking["start"] - timedelta(hours=20)) == {"day": 1, "hours": 0}
    assert reminders_at(booking["start"] - timedelta(hours=19)) == {"day": 0, "hours": 0}  # une seule fois
    assert len(titles(booking["client"])) == 1
    with SessionLocal() as db:
        mail = db.scalar(select(MessageOutbox).where(MessageOutbox.to_address == "cliente@test.kyja", MessageOutbox.subject.like("%rendez-vous%")))
    assert mail is not None and "TEST Brushing" in mail.body and f"/bookings/{booking['id']}" in mail.body


def test_two_hours_before_reminder_comes_after_the_day_one(booking):
    assert reminders_at(booking["start"] - timedelta(hours=20))["day"] == 1
    assert reminders_at(booking["start"] - timedelta(hours=1, minutes=50)) == {"day": 0, "hours": 1}
    assert reminders_at(booking["start"] - timedelta(hours=1)) == {"day": 0, "hours": 0}
    assert len(titles(booking["client"])) == 2


def test_missed_day_window_is_not_caught_up(booking):
    # le planificateur était arrêté toute la journée : à 1 h 50 on envoie seulement le rappel court
    assert reminders_at(booking["start"] - timedelta(hours=1, minutes=50)) == {"day": 0, "hours": 1}
    assert reminders_at(booking["start"] - timedelta(hours=1)) == {"day": 0, "hours": 0}
    assert len(titles(booking["client"])) == 1


def test_late_bookings_get_no_reminder_and_cancelled_ones_neither(booking):
    with SessionLocal() as db:
        db.execute(update(Appointment).where(Appointment.id == booking["id"]).values(created_at=booking["start"] - timedelta(hours=5)))
        db.commit()
    assert reminders_at(booking["start"] - timedelta(hours=20)) == {"day": 0, "hours": 0}  # réservé 5 h avant : pas de rappel de la veille
    with SessionLocal() as db:
        db.execute(
            update(Appointment)
            .where(Appointment.id == booking["id"])
            .values(status=AppointmentStatus.cancelled, reminder_day_sent_at=None, reminder_hours_sent_at=None)
        )
        db.commit()
    assert reminders_at(booking["start"] - timedelta(hours=1, minutes=30)) == {"day": 0, "hours": 0}
    assert titles(booking["client"]) == []


def test_reminder_respects_the_client_channel_choices(booking):
    with SessionLocal() as db:
        db.execute(update(User).where(User.id == booking["client"]).values(pref_email=False, pref_in_app=True))
        db.commit()
    reminders_at(booking["start"] - timedelta(hours=20))
    with SessionLocal() as db:
        assert db.scalars(select(MessageOutbox).where(MessageOutbox.user_id == booking["client"])).all() == []
    assert len(titles(booking["client"])) == 1


# --- envoi par Celery -----------------------------------------------------------------------------------------------


@pytest.fixture
def queued_mail(booking):
    with SessionLocal() as db:
        row = MessageOutbox(user_id=booking["client"], channel="email", to_address="cliente@test.kyja", subject="Test", body="Bonjour")
        db.add(row)
        db.commit()
        return row.id


def status(mail_id):
    with SessionLocal() as db:
        return db.get(MessageOutbox, mail_id).status


def test_celery_path_delivers_messages(queued_mail, monkeypatch):
    monkeypatch.setattr(dispatch, "celery_enabled", lambda: True)
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)  # sans Redis : la tâche s'exécute sur place
    dispatch.send_messages([queued_mail])
    assert status(queued_mail) == "preview"


def test_site_never_depends_on_the_broker(queued_mail, monkeypatch):
    monkeypatch.setattr(dispatch, "celery_enabled", lambda: True)

    def broker_down(*a, **k):
        raise ConnectionError("Redis est arrêté")

    monkeypatch.setattr(jobs.deliver_messages, "delay", broker_down)
    dispatch.send_messages([queued_mail])  # repli : envoi dans le processus du site
    assert status(queued_mail) == "preview"


def test_without_celery_messages_are_sent_in_the_process(queued_mail, monkeypatch):
    monkeypatch.setattr(dispatch, "celery_enabled", lambda: False)
    dispatch.send_messages([queued_mail])
    assert status(queued_mail) == "preview"


def test_failed_messages_are_retried_up_to_three_times(queued_mail, monkeypatch):
    def boom(*a, **k):
        raise providers.DeliveryError("Resend : 500")

    monkeypatch.setattr(providers, "send_email", boom)
    with SessionLocal() as db:
        assert notifications.deliver(db, [queued_mail]) == [queued_mail]
        assert notifications.deliver(db, [queued_mail]) == [queued_mail]
        assert notifications.deliver(db, [queued_mail]) == []  # 3e échec : on arrête, visible dans l'admin
        assert db.get(MessageOutbox, queued_mail).attempts == 3
    assert status(queued_mail) == "failed"


def test_stuck_queued_messages_are_picked_up(queued_mail):
    with SessionLocal() as db:
        assert notifications.retry_stuck(db) == 0  # trop récent : peut-être en cours d'envoi
        db.execute(update(MessageOutbox).where(MessageOutbox.id == queued_mail).values(created_at=datetime.now(timezone.utc) - timedelta(minutes=30)))
        db.commit()
        assert notifications.retry_stuck(db) == 1
    assert status(queued_mail) == "preview"


def test_every_scheduled_job_exists_and_runs(booking):
    for entry in celery_app.conf.beat_schedule.values():
        assert entry["task"] in celery_app.tasks, entry["task"]
    assert jobs.send_reminders.run() == {"day": 0, "hours": 0}  # rien dans les 24 h à venir
    assert set(jobs.purge_drafts.run()) == {"drafts", "photos"}
    assert jobs.retry_stuck_messages.run() == 0
    assert isinstance(jobs.expire_quotes.run(), int)
