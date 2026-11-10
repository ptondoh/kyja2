"""Déplacer un rendez-vous confirmé, avertir la coiffeuse, pagination et filtres des listes."""
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models.core import Appointment, AppointmentStatus, MessageOutbox, Notification, User
from app.main import app
from tests.conftest import csrf, local_start, login, make_admin, make_hairdresser, make_service, next_weekday, register
from tests.test_custom_quotes import appt, book, staff, visitor


@pytest.fixture
def setup():
    with SessionLocal() as db:
        marie = make_hairdresser(db, "Marie", "1.00", slots=tuple((d, 540, 1020) for d in range(7)))
        return {"marie": marie.id, "catalog": make_service(db, "TEST Brushing", base=60, price="55.00").id}


@pytest.fixture
def booked(setup):
    cl = visitor()
    register(cl, email="cliente@test.kyja")
    return {"cl": cl, "appt": book(cl, setup, key="catalog"), **setup}


def move(c, appt_id, day, hour):
    return c.post(f"/bookings/{appt_id}/reschedule", data={"slot": local_start(day, hour).isoformat(), "csrf_token": csrf(c)})


def test_client_moves_a_confirmed_appointment_and_hairdresser_is_told(booked):
    before = appt(booked["appt"])
    r = move(booked["cl"], booked["appt"], next_weekday(1), 14)
    assert r.status_code == 303 and r.headers["location"].endswith("?moved=1")
    after = appt(booked["appt"])
    assert after.starts_at == local_start(next_weekday(1), 14).astimezone(before.starts_at.tzinfo)
    assert after.status == AppointmentStatus.confirmed and after.computed_duration_min == before.computed_duration_min
    with SessionLocal() as db:
        marie = db.scalar(select(User).where(User.email == "marie@test.kyja"))
        titles = db.scalars(select(Notification.title).where(Notification.user_id == marie.id)).all()
    assert any("déplacé" in t for t in titles)


def test_reschedule_pages_and_slots_work(booked):
    c = booked["cl"]
    page = c.get(f"/bookings/{booked['appt']}/reschedule")
    assert page.status_code == 200 and "Changer la date ou l'heure" in page.text and f"/bookings/{booked['appt']}/reschedule/slots" in page.text
    day = next_weekday(1)
    slots = c.get(f"/bookings/{booked['appt']}/reschedule/slots", params={"day": day.isoformat()})
    assert slots.status_code == 200 and 'name="slot"' in slots.text
    assert c.get(f"/bookings/{booked['appt']}").text.count("Changer la date ou l'heure") == 1


def test_own_slot_can_be_reused_when_moving_by_a_few_minutes(booked):
    # le créneau actuel ne bloque pas le déplacement vers un horaire qui le chevauche
    r = move(booked["cl"], booked["appt"], next_weekday(0), 11)
    assert r.status_code == 303 and r.headers["location"].endswith("?moved=1")


def test_cannot_move_onto_someone_elses_slot(booked, setup):
    other = visitor()
    register(other, email="autre@test.kyja")
    book(other, setup, key="catalog", hour=14, day=next_weekday(1))
    r = move(booked["cl"], booked["appt"], next_weekday(1), 14)
    assert r.status_code == 303 and "error=" in r.headers["location"]
    assert appt(booked["appt"]).starts_at == local_start(next_weekday(0), 10).astimezone(appt(booked["appt"]).starts_at.tzinfo)


def test_others_cannot_move_it_and_cancelled_cannot_move(booked):
    other = visitor()
    register(other, email="autre@test.kyja")
    assert other.get(f"/bookings/{booked['appt']}/reschedule").status_code == 404
    assert move(other, booked["appt"], next_weekday(1), 14).status_code == 404
    booked["cl"].post(f"/bookings/{booked['appt']}/cancel", data={"csrf_token": csrf(booked["cl"])})
    r = booked["cl"].get(f"/bookings/{booked['appt']}/reschedule")
    assert r.status_code == 303 and r.headers["location"] == f"/bookings/{booked['appt']}"
    assert "error=" in move(booked["cl"], booked["appt"], next_weekday(1), 14).headers["location"]


def test_cancel_by_client_alerts_hairdresser_and_emails_her(booked):
    with SessionLocal() as db:
        marie = db.scalar(select(User).where(User.email == "marie@test.kyja"))
        marie.pref_email = True
        db.commit()
        mid = marie.id
    booked["cl"].post(f"/bookings/{booked['appt']}/cancel", data={"csrf_token": csrf(booked["cl"])})
    with SessionLocal() as db:
        assert any("annulé" in t for t in db.scalars(select(Notification.title).where(Notification.user_id == mid)))
        mails = db.scalars(select(MessageOutbox).where(MessageOutbox.user_id == mid, MessageOutbox.channel == "email").order_by(MessageOutbox.id)).all()
    assert mails and "annulé" in mails[-1].subject


def test_admin_appointments_filter_and_paginate(booked, setup):
    a = visitor()
    make_admin(a)
    html = a.get("/admin/appointments").text
    assert "Cliente" in html and 'name="statut"' in html
    only_cancelled = a.get("/admin/appointments", params={"statut": "cancelled"}).text
    assert "Aucun rendez-vous" in only_cancelled
    assert "Aucun rendez-vous" not in a.get("/admin/appointments", params={"statut": "confirmed", "type": "catalog"}).text
    assert "Aucun rendez-vous" in a.get("/admin/appointments", params={"q": "personne-inconnue"}).text
    assert a.get("/admin/appointments", params={"page": "abc"}).status_code == 200  # page invalide : ramenée à 1
    assert a.get("/admin/appointments", params={"page": "999", "statut": "zzz"}).status_code == 200


def test_pagination_helper_and_long_lists():
    from app.pagination import like, paginate_list

    p = paginate_list(list(range(45)), "3", per_page=20)
    assert (p.page, p.pages, p.total, p.first, p.last, p.items) == (3, 3, 45, 41, 45, [40, 41, 42, 43, 44])
    assert paginate_list([], "7").page == 1 and paginate_list(list(range(5)), "-4").page == 1
    assert p.window == [1, 2, 3]
    assert like("50%_x") == "%50\%\_x%"
    assert paginate_list(list(range(400)), "10", per_page=20).window == [1, None, 8, 9, 10, 11, 12, None, 20]


def test_admin_lists_search_and_messages_filter(booked):
    a = visitor()
    make_admin(a)
    assert "TEST Brushing" in a.get("/admin/services", params={"q": "brush"}).text
    assert "Aucune prestation" in a.get("/admin/services", params={"q": "zzzz"}).text
    assert "Aucun produit" in a.get("/admin/products", params={"q": "zzzz"}).text
    assert a.get("/admin/products", params={"stock": "bas"}).status_code == 200
    assert "Marie" in a.get("/admin/hairdressers", params={"q": "marie"}).text
    assert a.get("/admin/messages", params={"canal": "sms", "statut": "failed"}).status_code == 200
    assert a.get("/notifications", params={"page": "2"}).status_code in (200, 303)
