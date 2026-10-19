import html
import re
from decimal import Decimal
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models.core import Appointment, AppointmentStatus, Hairdresser, PricingMode, User
from app.main import app
from tests.conftest import (
    csrf,
    local_start,
    login,
    make_admin,
    make_hairdresser,
    make_service,
    next_weekday,
    register,
)


@pytest.fixture
def setup():
    with SessionLocal() as db:
        marie = make_hairdresser(db, "Marie", "1.30")
        sonia = make_hairdresser(db, "Sonia", "1.00")
        catalog = make_service(db, "TEST Brushing", base=60, buffer=10, price="55.00")
        custom = make_service(db, "TEST Sur mesure", base=120, mode=PricingMode.quote)
        return {"marie": marie.id, "sonia": sonia.id, "catalog": catalog.id, "custom": custom.id}


def other_client():
    c = TestClient(app, follow_redirects=False)
    c.get("/auth/login")
    return c


def book(c, setup, key="catalog", who="marie", hour=10, notes=""):
    """Parcours complet : récapitulatif puis confirmation. Retourne la réponse de confirmation."""
    day = next_weekday(0)
    start = local_start(day, hour)
    hid = "any" if who == "any" else str(setup[who])
    rv = c.post("/bookings/review", data={"service_id": setup[key], "hairdresser": hid, "slot": start.isoformat(),
                                          "notes": notes, "csrf_token": csrf(c)})  # fmt: skip
    assert rv.status_code == 200, rv.text[:300]
    return c.post("/bookings", data={"service_id": setup[key], "hairdresser": hid, "start": start.isoformat(),
                                     "notes": notes, "csrf_token": csrf(c)})  # fmt: skip


# --- accès et redirection -------------------------------------------------------------------


def test_pick_page_is_public_and_confirmation_needs_login_then_returns(client, setup):
    assert client.get(f"/bookings/new/{setup['catalog']}").status_code == 200  # aucune connexion avant la confirmation
    # Confirmer sans être connectée (ex. session expirée) : le choix est gardé, retour prévu sur /bookings/resume.
    start = local_start(next_weekday(0), 10)
    r = client.post("/bookings", data={"service_id": setup["catalog"], "hairdresser": setup["marie"],
                                       "start": start.isoformat(), "csrf_token": csrf(client)})  # fmt: skip
    assert r.status_code == 303
    assert r.headers["location"] == f"/auth/login?next={quote('/bookings/resume')}"
    assert "booking_draft" in r.headers.get("set-cookie", "")


def test_login_next_redirects_to_page_and_blocks_open_redirect(client):
    register(other := other_client(), email="n@test.kyja")
    login_page = client.get("/auth/login?next=/bookings/new/1")
    assert 'value="/bookings/new/1"' in login_page.text
    r = client.post("/auth/login", data={"email": "n@test.kyja", "password": "correct horse battery",
                                         "next": "/bookings/new/1", "csrf_token": csrf(client)})  # fmt: skip
    assert r.headers["location"] == "/bookings/new/1"
    for evil in ("//evil.example", "https://evil.example", "/\\evil.example"):
        c = other_client()
        r = c.post("/auth/login", data={"email": "n@test.kyja", "password": "correct horse battery",
                                        "next": evil, "csrf_token": csrf(c)})  # fmt: skip
        assert r.headers["location"] == "/", evil


def test_slots_endpoint_is_public(client, setup):
    r = client.get(f"/bookings/slots?service_id={setup['catalog']}&hairdresser=any&day={next_weekday(0).isoformat()}")
    assert r.status_code == 200 and "9 h" in r.text


def test_post_requires_csrf(client, setup):
    register(client)
    r = client.post("/bookings/review", data={"service_id": setup["catalog"], "hairdresser": "any", "slot": "x"})
    assert r.status_code == 403


# --- parcours -------------------------------------------------------------------------------------


def test_mode_pages(client, setup):
    assert "Choisir une coiffure" in client.get("/bookings/new").text
    cat = client.get("/bookings/new?mode=catalog").text
    cus = client.get("/bookings/new?mode=custom").text
    assert "TEST Brushing" in cat and "TEST Sur mesure" not in cat
    assert "TEST Sur mesure" in cus and "TEST Brushing" not in cus
    assert "55.00 $ CAD" in cat and "estimé" in cat
    assert "Prix à confirmer au salon" in cus


def test_pick_page_shows_duration_per_hairdresser(client, setup):
    register(client)
    page = client.get(f"/bookings/new/{setup['catalog']}").text
    # 60 min x 1,30 = 78 -> 90 (pas de 15) + 10 de repos = 1 h 40 ; Sonia : 60 + 10 = 1 h 10
    assert "Marie" in page and "1 h 40" in page
    assert "Sonia" in page and "1 h 10" in page
    assert "Peu importe" in page


def test_slots_fragment_changes_with_hairdresser(client, setup):
    register(client)
    day = next_weekday(0).isoformat()
    q = f"/bookings/slots?service_id={setup['catalog']}&day={day}"
    marie = client.get(q + f"&hairdresser={setup['marie']}").text
    sonia = client.get(q + f"&hairdresser={setup['sonia']}").text
    assert "1 h 40" in marie and "1 h 10" in sonia
    # Marie (1 h 40) ne peut pas commencer à 15 h 30 (fin 17 h 10) ; Sonia (1 h 10) le peut.
    assert "15 h 30" not in marie and "15 h 30" in sonia
    assert "Choisissez un jour dans le calendrier" in client.get(q.replace(day, "pas-une-date") + "&hairdresser=any").text


def test_full_booking_flow_catalog_mode(client, setup):
    register(client)
    r = book(client, setup)
    assert r.status_code == 303 and r.headers["location"].endswith("?new=1")
    detail = client.get(r.headers["location"]).text
    assert "Rendez-vous confirmé" in detail and "TEST Brushing" in detail and "Marie" in detail
    assert "55.00 $ CAD" in detail and "estimation" in detail
    assert "10 h → 11 h 40" in detail  # 1 h 40 calculée
    mine = client.get("/bookings").text
    assert "TEST Brushing" in mine and "À venir" in mine


def test_review_shows_calculation_and_price(client, setup):
    register(client)
    day = next_weekday(0)
    rv = client.post("/bookings/review", data={"service_id": setup["catalog"], "hairdresser": setup["marie"],
                                               "slot": local_start(day, 10).isoformat(), "csrf_token": csrf(client)})  # fmt: skip
    assert "1 h 40" in rv.text and "10 h → 11 h 40" in rv.text
    assert "55.00 $ CAD" in rv.text and "estimation" in rv.text
    assert "1 h × 1.30" in rv.text and "10 min de repos" in rv.text


def test_custom_mode_requires_description_and_has_no_price(client, setup):
    register(client)
    day = next_weekday(0)
    data = {"service_id": setup["custom"], "hairdresser": setup["sonia"], "slot": local_start(day, 9).isoformat(), "csrf_token": csrf(client)}
    assert client.post("/bookings/review", data={**data, "notes": "court"}).status_code == 400
    ok = client.post("/bookings/review", data={**data, "notes": "Tresses collées avec rajouts, longueur taille"})
    assert ok.status_code == 200 and "À confirmer au salon" in ok.text
    r = book(client, setup, key="custom", who="sonia", hour=9, notes="Tresses collées avec rajouts, longueur taille")
    detail = client.get(r.headers["location"]).text
    assert "À confirmer au salon" in detail and "Tresses collées" in detail
    with SessionLocal() as db:
        a = db.scalar(select(Appointment).where(Appointment.service_id == setup["custom"]))
    assert a.estimated_price is None and a.booking_mode.value == "custom" and a.computed_duration_min == 120


def test_any_hairdresser_flow_assigns_and_second_gets_other(client, setup):
    register(client)
    r1 = book(client, setup, who="any", hour=10)
    c2 = other_client()
    register(c2, email="b@test.kyja")
    r2 = book(c2, setup, who="any", hour=10)
    assert r1.status_code == r2.status_code == 303
    with SessionLocal() as db:
        ids = {a.hairdresser_id for a in db.scalars(select(Appointment))}
    assert ids == {setup["marie"], setup["sonia"]}


def test_form_tampering_cannot_change_price_or_duration(client, setup):
    register(client)
    day = next_weekday(0)
    r = client.post("/bookings", data={
        "service_id": setup["catalog"], "hairdresser": setup["marie"], "start": local_start(day, 10).isoformat(),
        "csrf_token": csrf(client),
        # champs forgés : ignorés, tout est recalculé côté serveur
        "price": "1.00", "estimated_price": "1.00", "final_price": "0.01", "computed_duration_min": "15",
        "coefficient_used": "0.50", "status": "completed", "client_id": "1",
    })  # fmt: skip
    assert r.status_code == 303
    with SessionLocal() as db:
        a = db.scalar(select(Appointment).where(Appointment.service_id == setup["catalog"]))
        me = db.scalar(select(User).where(User.email == "a@test.kyja"))
    assert a.estimated_price == Decimal("55.00") and a.final_price is None
    assert a.computed_duration_min == 100 and a.coefficient_used == Decimal("1.30")
    assert a.status == AppointmentStatus.confirmed and a.client_id == me.id


def test_slot_taken_between_review_and_confirm_gives_conflict_page(client, setup):
    register(client)
    other = other_client()
    register(other, email="b@test.kyja")
    book(other, setup, hour=10)
    day = next_weekday(0)
    r = client.post("/bookings", data={"service_id": setup["catalog"], "hairdresser": setup["marie"],
                                       "start": local_start(day, 10).isoformat(), "csrf_token": csrf(client)})  # fmt: skip
    assert r.status_code == 409 and "vient d'être réservé" in html.unescape(r.text)
    with SessionLocal() as db:
        assert len(db.scalars(select(Appointment)).all()) == 1


def test_cannot_book_without_slot_or_with_garbage(client, setup):
    register(client)
    assert client.post("/bookings/review", data={"service_id": setup["catalog"], "hairdresser": "any", "slot": "",
                                                 "csrf_token": csrf(client)}).status_code == 400  # fmt: skip
    for bad in ("not-a-date", "2030-01-07T10:00:00"):  # sans fuseau
        r = client.post("/bookings/review", data={"service_id": setup["catalog"], "hairdresser": "any", "slot": bad,
                                                  "csrf_token": csrf(client)})  # fmt: skip
        assert r.status_code == 400
    r = client.post("/bookings/review", data={"service_id": setup["catalog"], "hairdresser": "abc", "slot": "x",
                                              "csrf_token": csrf(client)})  # fmt: skip
    assert r.status_code == 400
    assert client.get("/bookings/new/999999").status_code in (404,)


# --- propriété des rendez-vous ----------------------------------------------------------------------


def test_other_client_cannot_see_or_cancel_my_booking(client, setup):
    register(client)
    url = book(client, setup).headers["location"].split("?")[0]
    other = other_client()
    register(other, email="b@test.kyja")
    assert other.get(url).status_code == 404
    assert other.post(url + "/cancel", data={"csrf_token": csrf(other)}).status_code == 404
    assert "TEST Brushing" not in other.get("/bookings").text


def test_cancel_by_owner_frees_slot(client, setup):
    register(client)
    url = book(client, setup).headers["location"].split("?")[0]
    r = client.post(url + "/cancel", data={"csrf_token": csrf(client)})
    assert r.status_code == 303 and "cancelled=1" in r.headers["location"]
    assert "Rendez-vous annulé" in client.get(r.headers["location"]).text
    assert book(client, setup).status_code == 303  # le créneau est de nouveau libre


# --- administration ---------------------------------------------------------------------------------------


def test_admin_hairdresser_management(client):
    make_admin(client)
    base = {"csrf_token": csrf(client)}
    bad = client.post("/admin/hairdressers", data={**base, "full_name": "TEST X", "email": "x@test.kyja", "password": "court", "coefficient": "9"})
    assert bad.status_code == 400 and "Le coefficient doit être entre" in bad.text
    ok = client.post("/admin/hairdressers", data={**base, "full_name": "TEST Lea", "email": "lea@test.kyja",
                                                  "password": "mot de passe provisoire", "coefficient": "1,25"})  # fmt: skip
    assert ok.status_code == 303
    hid = int(re.search(r"/hairdressers/(\d+)", ok.headers["location"]).group(1))
    with SessionLocal() as db:
        h = db.get(Hairdresser, hid)
        assert h.duration_coefficient == Decimal("1.25") and h.user.role.value == "hairdresser"
    # horaires : ajout, chevauchement refusé, format invalide refusé
    assert client.post(f"/admin/hairdressers/{hid}/slots", data={**base, "weekday": 0, "start": "09:00", "end": "12:00"}).status_code == 303
    assert client.post(f"/admin/hairdressers/{hid}/slots", data={**base, "weekday": 0, "start": "11:00", "end": "13:00"}).status_code == 400
    assert client.post(f"/admin/hairdressers/{hid}/slots", data={**base, "weekday": 0, "start": "25:00", "end": "26:00"}).status_code == 400
    assert client.post(f"/admin/hairdressers/{hid}/slots", data={**base, "weekday": 0, "start": "14:00", "end": "13:00"}).status_code == 400
    assert "Lundi" in client.get(f"/admin/hairdressers/{hid}").text
    # le coefficient se modifie
    assert client.post(f"/admin/hairdressers/{hid}", data={**base, "coefficient": "0.9", "is_active": "true"}).status_code == 303
    assert client.post(f"/admin/hairdressers/{hid}", data={**base, "coefficient": "5"}).status_code == 400
    # un mot de passe trop court / un email déjà pris sont refusés
    dup = client.post("/admin/hairdressers", data={**base, "full_name": "TEST Y", "email": "lea@test.kyja",
                                                   "password": "mot de passe provisoire", "coefficient": "1"})  # fmt: skip
    assert dup.status_code == 400 and "existe déjà" in dup.text


def test_client_cannot_use_booking_admin(client):
    register(client)
    for path in ("/admin/hairdressers", "/admin/appointments", "/admin/resources"):
        assert client.get(path).status_code == 403
    r = client.post("/admin/hairdressers", data={"full_name": "x", "csrf_token": csrf(client)})
    assert r.status_code == 403


def test_admin_final_price_flow_visible_to_client(client, setup):
    register(client)
    url = book(client, setup).headers["location"].split("?")[0]
    appt_id = int(url.rsplit("/", 1)[1])
    admin = other_client()
    make_admin(admin)
    page = admin.get("/admin/appointments").text
    assert "TEST Brushing" in page and "55.00 $ CAD" in page and "1 h 40" in page
    assert admin.post(f"/admin/appointments/{appt_id}/final-price", data={"price": "abc", "csrf_token": csrf(admin)}).status_code == 400
    assert admin.post(f"/admin/appointments/{appt_id}/final-price", data={"price": "72,50", "csrf_token": csrf(admin)}).status_code == 303
    detail = client.get(url).text
    assert "72.50 $ CAD" in detail and "montant final" in detail and "Terminé" in detail
    assert "Annuler ce rendez-vous" not in detail


def test_admin_resources_and_service_requirements(client):
    make_admin(client)
    base = {"csrf_token": csrf(client)}
    assert client.post("/admin/resources", data={**base, "kind": "Bad Kind!", "name": "TEST Bac"}).status_code == 400
    assert client.post("/admin/resources", data={**base, "kind": "wash_basin", "name": "TEST Bac 1"}).status_code == 303
    assert "TEST Bac 1" in client.get("/admin/resources").text
    r = client.post("/admin/services", data={**base, "name": "TEST Shampoing", "base_duration_min": "30", "price": "20",
                                             "pricing_mode": "estimate", "required_kinds": "wash_basin, dryer_hood", "is_active": "true"})  # fmt: skip
    assert r.status_code == 303
    page = client.get(r.headers["location"].split("?")[0]).text
    assert "dryer_hood, wash_basin" in page
    bad = client.post("/admin/services", data={**base, "name": "TEST Z", "base_duration_min": "30", "price": "20",
                                               "required_kinds": "Pas Valide"})  # fmt: skip
    assert bad.status_code == 400


def test_admin_quote_service_stores_no_price(client):
    make_admin(client)
    r = client.post("/admin/services", data={"csrf_token": csrf(client), "name": "TEST Devis", "base_duration_min": "90",
                                             "pricing_mode": "quote", "price": "999", "is_active": "true"})  # fmt: skip
    assert r.status_code == 303
    from app.db.models.core import Service

    with SessionLocal() as db:
        s = db.scalar(select(Service).where(Service.name == "TEST Devis"))
    assert s.pricing_mode == PricingMode.quote and s.price is None  # aucun prix promis


def test_admin_calendar_settings_validation(client):
    make_admin(client)
    base = {"csrf_token": csrf(client), "video_max_mb": "100", "video_max_seconds": "180"}
    assert client.post("/admin/settings", data={**base, "calendar_step_min": "7"}).status_code == 400  # ne divise pas 60
    ok = client.post("/admin/settings", data={**base, "calendar_step_min": "30", "booking_horizon_days": "30", "booking_min_notice_min": "120"})
    assert ok.status_code == 303
    assert "30" in client.get("/admin/settings").text
