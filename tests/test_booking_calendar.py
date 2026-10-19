"""Calendrier mensuel et « Demande sur mesure » intégrée, depuis l'état de départ réel de l'application."""
import re
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.bookings import engine
from app.db.base import SessionLocal
from app.db.models.core import Appointment, Hairdresser, PricingMode, Service
from app.main import app
from tests.conftest import (
    csrf,
    local_start,
    make_admin,
    make_hairdresser,
    make_service,
    next_weekday,
    register,
)

NOTES = "Tresses africaines avec rajouts, longueur milieu du dos, couleur noir et bordeaux"


@pytest.fixture(autouse=True)
def _restore_builtin():
    """La demande intégrée est partagée : on la remet dans son état d'origine après chaque test."""
    yield
    with SessionLocal() as db:
        svc = db.scalar(select(Service).where(Service.is_custom_request.is_(True)))
        if svc:
            svc.base_duration_min, svc.buffer_min, svc.is_active = 120, 15, True
            db.commit()


def builtin_id() -> int:
    with SessionLocal() as db:
        return engine.custom_request_service(db).id


def month_days(html: str) -> list[date]:
    """Jours cliquables du calendrier rendu."""
    return [date.fromisoformat(v) for v in re.findall(r'name="day" value="([\d-]+)"', html)]


def other_client(email="b@test.kyja"):
    c = TestClient(app, follow_redirects=False)
    c.get("/auth/login")
    register(c, email=email)
    return c


# --- état de départ : aucune prestation créée à la main ------------------------------------------------


def test_custom_mode_goes_straight_to_the_form_on_a_fresh_install(client):
    """Cas réel d'une installation neuve : aucune prestation « sur devis » n'existe."""
    r = client.get("/bookings/new?mode=custom")
    assert r.status_code == 303 and r.headers["location"] == f"/bookings/new/{builtin_id()}"
    r = client.get(r.headers["location"])
    assert r.status_code == 303 and "/auth/login?next=" in r.headers["location"]  # connexion puis retour


def test_custom_form_has_description_photos_and_calendar(client):
    with SessionLocal() as db:
        make_hairdresser(db, "Marie")
    register(client)
    page = client.get(f"/bookings/new/{builtin_id()}").text
    assert 'name="notes"' in page and "required" in page and "Décrivez la coiffure souhaitée" in page  # zone de texte
    assert 'id="photo-input"' in page and 'data-max="10"' in page and "booking-photos.js" in page  # photos (10 max)
    assert 'class="cal"' in page and 'id="calendar"' in page and 'id="slots"' in page  # calendrier + heures
    assert page.count('id="slots"') == 1  # la remise à zéro hors-bande n'est pas dupliquée dans la page
    assert "Prix à confirmer au salon" in page


def test_builtin_service_is_hidden_from_the_public_catalog(client):
    bid = builtin_id()
    assert "Demande sur mesure" not in client.get("/services").text
    assert "Demande sur mesure" not in client.get("/").text
    assert client.get(f"/services/{bid}").status_code == 404
    with SessionLocal() as db:
        make_service(db, "TEST Coiffure", price="40.00")
    assert "Demande sur mesure" not in client.get("/bookings/new?mode=catalog").text


def test_custom_list_offers_other_request_when_admin_created_quote_services(client):
    with SessionLocal() as db:
        make_service(db, "TEST Tresses", base=180, mode=PricingMode.quote)
    r = client.get("/bookings/new?mode=custom")
    assert r.status_code == 200 and "TEST Tresses" in r.text and "Autre demande" in r.text
    assert f"/bookings/new/{builtin_id()}" in r.text


def test_builtin_is_recreated_if_missing(client):
    with SessionLocal() as db:
        db.execute(delete(Service).where(Service.is_custom_request.is_(True)))
        db.commit()
        svc = engine.custom_request_service(db)
        assert svc.is_custom_request and svc.pricing_mode == PricingMode.quote and svc.price is None
        assert engine.custom_request_service(db).id == svc.id  # une seule, jamais de doublon
        assert len(db.scalars(select(Service).where(Service.is_custom_request.is_(True))).all()) == 1


def test_admin_can_edit_builtin_duration_and_it_drives_the_calculation(client):
    with SessionLocal() as db:
        make_hairdresser(db, "Sonia", "1.00")
        make_hairdresser(db, "Marie", "1.50")
    bid = builtin_id()
    admin = other_client("admin2@test.kyja")  # simple cliente d'abord : accès admin refusé
    assert admin.get(f"/admin/services/{bid}").status_code == 403
    root = TestClient(app, follow_redirects=False)
    root.get("/auth/login")
    make_admin(root)
    assert "intégrée" in root.get("/admin/services").text
    base = {"csrf_token": csrf(root), "name": "Demande sur mesure", "base_duration_min": "180", "buffer_min": "15",
            "pricing_mode": "quote", "is_active": "true"}  # fmt: skip
    assert root.post(f"/admin/services/{bid}", data=base).status_code == 303
    page = admin.get(f"/bookings/new/{bid}").text
    assert "3 h 15" in page  # Sonia : 180 min x 1,00 + 15
    assert "4 h 45" in page  # Marie : 180 x 1,50 = 270 min + 15


def test_full_custom_booking_on_builtin_service(client):
    with SessionLocal() as db:
        h = make_hairdresser(db, "Sonia", "1.00")
    register(client)
    start = local_start(next_weekday(0), 10)
    r = client.post("/bookings", data={"service_id": builtin_id(), "hairdresser": h.id, "start": start.isoformat(),
                                       "notes": NOTES, "csrf_token": csrf(client)})  # fmt: skip
    assert r.status_code == 303
    with SessionLocal() as db:
        a = db.scalar(select(Appointment))
        assert a.booking_mode.value == "custom" and a.estimated_price is None
        assert a.computed_duration_min == 135 and a.client_notes == NOTES  # 120 x 1,00 + 15


# --- calendrier -----------------------------------------------------------------------------------------


def test_calendar_requires_login_and_valid_hairdresser(client):
    bid = builtin_id()
    assert client.get(f"/bookings/calendar?service_id={bid}").status_code == 401
    register(client)
    assert client.get(f"/bookings/calendar?service_id={bid}&hairdresser=abc").status_code == 400
    assert client.get("/bookings/calendar?service_id=999999").status_code == 404


def test_only_open_days_are_clickable(client):
    with SessionLocal() as db:
        make_hairdresser(db, "Marie", slots=((0, 540, 1020),))  # lundi seulement
    register(client)
    now = engine.utcnow()
    days = month_days(client.get(f"/bookings/calendar?service_id={builtin_id()}&month={next_weekday(0).strftime('%Y-%m')}").text)
    assert days and all(d.weekday() == 0 for d in days)  # jamais un autre jour
    today = now.astimezone(engine.salon_tz()).date()
    assert all(d >= today for d in days)
    # la ligne de prestation intégrée dure 2 h 15 : elle tient dans 9 h-17 h
    assert next_weekday(0) in month_days(client.get(f"/bookings/calendar?service_id={builtin_id()}&month={next_weekday(0).strftime('%Y-%m')}").text)


def test_past_and_beyond_horizon_days_are_not_offered(client):
    with SessionLocal() as db:
        make_hairdresser(db, "Marie", slots=tuple((d, 540, 1020) for d in range(7)))
    register(client)
    bid = builtin_id()
    today = engine.utcnow().astimezone(engine.salon_tz()).date()
    this_month = month_days(client.get(f"/bookings/calendar?service_id={bid}&month={today.strftime('%Y-%m')}").text)
    assert this_month and min(this_month) >= today  # rien dans le passé
    far = (today + timedelta(days=400)).strftime("%Y-%m")
    clamped = client.get(f"/bookings/calendar?service_id={bid}&month={far}").text
    assert all(d <= today + timedelta(days=60) for d in month_days(clamped))  # plafonné à l'horizon (60 j)
    past = client.get(f"/bookings/calendar?service_id={bid}&month=2020-01").text
    assert today.strftime("%Y-%m") in past  # un mois passé revient au mois courant


def test_garbage_month_falls_back_to_current_month(client):
    with SessionLocal() as db:
        make_hairdresser(db, "Marie")
    register(client)
    r = client.get(f"/bookings/calendar?service_id={builtin_id()}&month=pas-un-mois")
    assert r.status_code == 200 and 'class="cal"' in r.text


def test_navigation_buttons_follow_the_allowed_range(client):
    with SessionLocal() as db:
        make_hairdresser(db, "Marie")
    register(client)
    bid = builtin_id()
    today = engine.utcnow().astimezone(engine.salon_tz()).date()
    first = client.get(f"/bookings/calendar?service_id={bid}&month={today.strftime('%Y-%m')}").text
    assert 'aria-label="Mois précédent"' not in first  # pas de recul avant le mois courant
    assert 'class="cal-nav is-off"' in first  # la flèche est présente mais inactive
    assert 'aria-label="Mois suivant"' in first
    nxt = (today.replace(day=1) + timedelta(days=32)).replace(day=1)
    second = client.get(f"/bookings/calendar?service_id={bid}&month={nxt.strftime('%Y-%m')}").text
    assert 'aria-label="Mois précédent"' in second


def test_fully_booked_day_becomes_unavailable(client):
    with SessionLocal() as db:
        h = make_hairdresser(db, "Marie", slots=((0, 9 * 60, 11 * 60 + 15),))  # 2 h 15 : un seul créneau pour la demande intégrée
    register(client)
    day = next_weekday(0)
    month = day.strftime("%Y-%m")
    bid = builtin_id()
    assert day in month_days(client.get(f"/bookings/calendar?service_id={bid}&month={month}").text)
    r = client.post("/bookings", data={"service_id": bid, "hairdresser": h.id, "start": local_start(day, 9).isoformat(),
                                       "notes": NOTES, "csrf_token": csrf(client)})  # fmt: skip
    assert r.status_code == 303
    assert day not in month_days(client.get(f"/bookings/calendar?service_id={bid}&month={month}").text)
    # annulation : le jour redevient disponible
    appt_url = r.headers["location"].split("?")[0]
    client.post(appt_url + "/cancel", data={"csrf_token": csrf(client)})
    assert day in month_days(client.get(f"/bookings/calendar?service_id={bid}&month={month}").text)


def test_hairdresser_choice_changes_available_days(client):
    with SessionLocal() as db:
        monday = make_hairdresser(db, "Lundi", slots=((0, 540, 1020),))
        tuesday = make_hairdresser(db, "Mardi", slots=((1, 540, 1020),))
    register(client)
    bid = builtin_id()
    month = next_weekday(0).strftime("%Y-%m")
    q = f"/bookings/calendar?service_id={bid}&month={month}"
    only_m = month_days(client.get(q + f"&hairdresser={monday.id}").text)
    only_t = month_days(client.get(q + f"&hairdresser={tuesday.id}").text)
    anyone = month_days(client.get(q + "&hairdresser=any").text)
    assert only_m and all(d.weekday() == 0 for d in only_m)
    assert only_t and all(d.weekday() == 1 for d in only_t)
    assert set(anyone) == set(only_m) | set(only_t)  # « peu importe » = union


def test_inactive_hairdresser_has_no_days(client):
    with SessionLocal() as db:
        h = make_hairdresser(db, "Marie")
        h.is_active = False
        db.commit()
    register(client)
    r = client.get(f"/bookings/calendar?service_id={builtin_id()}&hairdresser={h.id}&month={next_weekday(0).strftime('%Y-%m')}")
    assert month_days(r.text) == []


def test_empty_salon_shows_a_clear_message_instead_of_a_blank_calendar(client):
    register(client)  # aucune coiffeuse configurée
    page = client.get(f"/bookings/new/{builtin_id()}").text
    assert "pas encore ouvert de créneaux en ligne" in page.replace("&#39;", "'")
    assert month_days(page) == []


def test_calendar_fragment_resets_hours_out_of_band(client):
    with SessionLocal() as db:
        make_hairdresser(db, "Marie")
    register(client)
    frag = client.get(f"/bookings/calendar?service_id={builtin_id()}").text
    assert 'hx-swap-oob="innerHTML"' in frag and 'id="slots"' in frag
    assert 'name="cal_month"' in frag  # le mois courant survit au changement de coiffeuse


def test_clicking_a_day_loads_its_hours(client):
    with SessionLocal() as db:
        make_hairdresser(db, "Marie", slots=((0, 9 * 60, 12 * 60),))
    register(client)
    day = next_weekday(0)
    r = client.get(f"/bookings/slots?service_id={builtin_id()}&hairdresser=any&day={day.isoformat()}")
    assert r.status_code == 200 and "9 h" in r.text and "9 h 45" in r.text  # 2 h 15 : dernier début 9 h 45
    starts = re.findall(r'name="slot" value="([^"]+)"', r.text)
    assert len(starts) == 4  # 9 h, 9 h 15, 9 h 30, 9 h 45
    labels = re.findall(r'<span>([^<]+)</span></label>', r.text)
    assert labels == ["9 h", "9 h 15", "9 h 30", "9 h 45"]
    assert all(datetime.fromisoformat(s).tzinfo is not None for s in starts)
