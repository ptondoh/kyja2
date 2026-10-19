"""Réservation sans connexion préalable : tout est public jusqu'à la confirmation (décision : option 1)."""
import html
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.bookings import drafts, engine
from app.bookings.engine import BookingError, SlotUnavailable
from app.bookings.router import browse_limiter
from app.db.base import SessionLocal
from app.db.models.core import Appointment, BookingDraft, BookingPhoto, Hairdresser, Role, Service, User
from app.main import app
from tests.conftest import (
    csrf,
    local_start,
    make_admin,
    make_client_user,
    make_hairdresser,
    make_service,
    next_weekday,
    register,
)
from tests.test_booking_photos import png, up

NOTES = "Tresses africaines avec rajouts, longueur milieu du dos, couleur noir et bordeaux"
RESUME = "/bookings/resume"


@pytest.fixture
def setup():
    with SessionLocal() as db:
        h = make_hairdresser(db, "Marie", "1.00")
        catalog = make_service(db, "TEST Brushing", base=60, price="55.00")
        custom = engine.custom_request_service(db)
        return {"h": h.id, "catalog": catalog.id, "custom": custom.id}


def visitor():
    """Visiteuse : aucun compte, aucun cookie de session, seulement le cookie CSRF."""
    c = TestClient(app, follow_redirects=False)
    c.get("/auth/login")
    return c


def review(c, setup, key="catalog", hour=10, notes="", photo_ids=(), slot=True):
    start = local_start(next_weekday(0), hour)
    data = {"service_id": setup[key], "hairdresser": str(setup["h"]), "notes": notes,
            "photo_ids": list(photo_ids), "csrf_token": csrf(c)}  # fmt: skip
    if slot:
        data["slot"] = start.isoformat()
    return start, c.post("/bookings/review", data=data)


def confirm_data(c, setup, start, key="catalog", notes="", photo_ids=()):
    return {"service_id": setup[key], "hairdresser": str(setup["h"]), "start": start.isoformat(),
            "notes": notes, "photo_ids": list(photo_ids), "csrf_token": csrf(c)}  # fmt: skip


def drafts_in_db():
    with SessionLocal() as db:
        return db.scalars(select(BookingDraft)).all()


# --- le parcours est public jusqu'au récapitulatif ------------------------------------------------------------


def test_whole_wizard_is_public_until_confirmation(setup):
    v = visitor()
    assert v.get("/bookings/new").status_code == 200
    assert v.get("/bookings/new?mode=catalog").status_code == 200
    page = v.get(f"/bookings/new/{setup['catalog']}")
    assert page.status_code == 200 and 'class="cal"' in page.text
    month = next_weekday(0).strftime("%Y-%m")
    assert v.get(f"/bookings/calendar?service_id={setup['catalog']}&month={month}").status_code == 200
    day = next_weekday(0).isoformat()
    assert v.get(f"/bookings/slots?service_id={setup['catalog']}&day={day}").status_code == 200
    start, r = review(v, setup)
    assert r.status_code == 200 and "Récapitulatif" in r.text  # même sans compte


def test_anonymous_review_shows_login_wall_not_a_confirm_button(setup):
    v = visitor()
    _, r = review(v, setup)
    text = html.unescape(r.text)
    assert 'id="login-wall"' in text and "Dernière étape" in text
    assert f"/auth/register?next={quote(RESUME)}" in text and f"/auth/login?next={quote(RESUME)}" in text
    assert "Confirmer la réservation" not in text  # impossible de confirmer sans compte
    assert 'action="/bookings"' not in text
    assert "55.00 $ CAD" in text and "Au salon" in text  # mais le récapitulatif est complet


def test_choice_is_saved_with_a_private_cookie(setup):
    v = visitor()
    start, r = review(v, setup, notes="Une note")
    cookie = r.headers["set-cookie"].lower()
    assert "booking_draft=" in cookie and "httponly" in cookie and "samesite=lax" in cookie and "path=/bookings" in cookie
    [row] = drafts_in_db()
    p = row.payload
    assert p["service_id"] == setup["catalog"] and p["hairdresser"] == str(setup["h"]) and p["notes"] == "Une note"
    assert datetime.fromisoformat(p["start"]) == start
    token = v.cookies.get("booking_draft")
    assert token and token not in str(row.payload) and row.token_hash == drafts.hash_token(token)  # seule l'empreinte est stockée


def test_register_then_resume_then_confirm(setup):
    v = visitor()
    start, _ = review(v, setup)
    r = register(v, email="nouvelle@test.kyja", next=RESUME)
    assert r.status_code == 303 and r.headers["location"] == RESUME  # retour à la réservation, pas à l'accueil
    page = v.get(RESUME)
    assert page.status_code == 200
    text = html.unescape(page.text)
    assert "Bon retour" in text and "Confirmer la réservation" in text and "TEST Brushing" in text
    assert f'value="{start.isoformat()}"' in text
    ok = v.post("/bookings", data=confirm_data(v, setup, start))
    assert ok.status_code == 303 and ok.headers["location"].endswith("?new=1")
    assert "Rendez-vous confirmé" in v.get(ok.headers["location"]).text
    with SessionLocal() as db:
        appt = db.scalar(select(Appointment))
        assert db.get(User, appt.client_id).email == "nouvelle@test.kyja"
    assert drafts_in_db() == []  # le brouillon a servi
    assert 'booking_draft=""' in ok.headers["set-cookie"] or "max-age=0" in ok.headers["set-cookie"].lower()


def test_login_with_existing_account_also_returns_to_the_recap(setup):
    existing = visitor()
    register(existing, email="ancienne@test.kyja")
    v = visitor()
    start, _ = review(v, setup)
    r = v.post("/auth/login", data={"email": "ancienne@test.kyja", "password": "correct horse battery",
                                    "next": RESUME, "csrf_token": csrf(v)})  # fmt: skip
    assert r.status_code == 303 and r.headers["location"] == RESUME
    assert "Confirmer la réservation" in html.unescape(v.get(RESUME).text)


def test_logged_in_client_keeps_the_direct_flow(setup):
    c = visitor()
    register(c)
    start, r = review(c, setup)
    assert 'id="login-wall"' not in r.text and "Confirmer la réservation" in html.unescape(r.text)
    assert drafts_in_db() == []  # pas de brouillon inutile quand on est déjà connectée


def test_resume_without_a_draft_goes_back_to_the_start(client):
    register(client)
    r = client.get(RESUME)
    assert r.status_code == 303 and r.headers["location"] == "/bookings/new"


def test_resume_requires_login_and_returns_to_resume(setup):
    v = visitor()
    review(v, setup)
    r = v.get(RESUME)
    assert r.status_code == 303 and r.headers["location"] == f"/auth/login?next={quote(RESUME)}"


def test_expired_draft_is_ignored(setup):
    v = visitor()
    review(v, setup)
    with SessionLocal() as db:
        db.execute(update(BookingDraft).values(created_at=datetime.now(timezone.utc) - timedelta(hours=25)))
        db.commit()
    register(v, email="tardive@test.kyja", next=RESUME)
    r = v.get(RESUME)
    assert r.status_code == 303 and r.headers["location"] == "/bookings/new"


def test_slot_taken_while_signing_up_gives_a_clear_conflict(setup):
    v = visitor()
    start, _ = review(v, setup)
    with SessionLocal() as db:
        engine.create_booking(db, client=make_client_user(db, "rapide@test.kyja"), service=db.get(Service, setup["catalog"]),
                              hairdresser_id=setup["h"], start=start)  # fmt: skip
    register(v, email="lente@test.kyja", next=RESUME)
    r = v.get(RESUME)
    assert r.status_code == 409 and "plus disponible" in html.unescape(r.text)
    assert 'class="cal"' in r.text  # on lui propose directement un autre créneau


def test_session_expiring_at_confirmation_keeps_the_choice(setup):
    c = visitor()
    register(c)
    start, _ = review(c, setup)
    c.cookies.delete("access_token")  # la session de 15 min est tombée
    r = c.post("/bookings", data=confirm_data(c, setup, start))
    assert r.status_code == 303 and r.headers["location"] == f"/auth/login?next={quote(RESUME)}"
    assert len(drafts_in_db()) == 1
    r = c.post("/auth/login", data={"email": "a@test.kyja", "password": "correct horse battery", "next": RESUME, "csrf_token": csrf(c)})
    assert "Confirmer la réservation" in html.unescape(c.get(RESUME).text)


def test_anonymous_validation_errors_do_not_create_drafts(setup):
    v = visitor()
    _, r = review(v, setup, slot=False)
    assert r.status_code == 400 and "Choisissez un créneau" in r.text
    _, r = review(v, setup, key="custom", notes="court")
    assert r.status_code == 400 and drafts_in_db() == []


# --- photos envoyées avant la connexion -------------------------------------------------------------------------------


def test_photos_sent_before_login_follow_the_client_after_login(setup):
    v = visitor()
    ids = [up(v, png(color=(i * 30, 80, 120))).json()["id"] for i in range(3)]
    start, r = review(v, setup, key="custom", notes=NOTES, photo_ids=ids)
    assert r.status_code == 200 and "Photos d'inspiration (3)" in r.text and 'id="login-wall"' in r.text
    assert "vos photos" in html.unescape(r.text)  # le mur de connexion le dit : rien n'est perdu
    register(v, email="photos@test.kyja", next=RESUME)
    page = v.get(RESUME)
    assert page.status_code == 200 and "Photos d'inspiration (3)" in page.text
    ok = v.post("/bookings", data=confirm_data(v, setup, start, key="custom", notes=NOTES, photo_ids=ids))
    assert ok.status_code == 303
    with SessionLocal() as db:
        appt = db.scalar(select(Appointment))
        photos = db.scalars(select(BookingPhoto)).all()
        assert sorted(p.id for p in photos) == sorted(ids)
        assert all(p.appointment_id == appt.id and p.client_id == appt.client_id and p.draft_token_hash is None for p in photos)
    assert "Photos d'inspiration (3)" in v.get(ok.headers["location"]).text


def test_anonymous_photo_is_visible_only_to_its_uploader(setup):
    v = visitor()
    pid = up(v, png()).json()["id"]
    assert v.get(f"/bookings/photos/{pid}").status_code == 200  # la visiteuse voit son aperçu
    assert visitor().get(f"/bookings/photos/{pid}").status_code == 404  # une autre visiteuse : rien
    stranger = visitor()
    register(stranger, email="curieuse@test.kyja")
    assert stranger.get(f"/bookings/photos/{pid}").status_code == 404  # ni une cliente connectée
    admin = visitor()
    make_admin(admin)
    assert admin.get(f"/bookings/photos/{pid}").status_code == 200  # l'admin voit tout


def test_one_visitors_photo_cannot_be_claimed_by_another(setup):
    a, b = visitor(), visitor()
    pid = up(a, png()).json()["id"]
    up(b, png(color=(5, 5, 5)))
    register(b, email="b@test.kyja")
    b.get(f"/bookings/new/{setup['custom']}")  # visite la page : rattache SES photos seulement
    with SessionLocal() as db:
        photo = db.get(BookingPhoto, pid)
        assert photo.client_id is None and photo.draft_token_hash is not None  # celle de A est restée à A


def test_anonymous_cannot_use_a_photo_id_from_someone_else(setup):
    a, b = visitor(), visitor()
    pid = up(a, png()).json()["id"]
    _, r = review(b, setup, key="custom", notes=NOTES, photo_ids=[pid])
    assert r.status_code == 400 and "plus disponible" in html.unescape(r.text)
    assert drafts_in_db() == []


def test_visitor_can_remove_her_own_draft_photo_only(setup):
    a, b = visitor(), visitor()
    pid = up(a, png()).json()["id"]
    assert b.post(f"/bookings/photos/{pid}/delete", headers={"x-csrf-token": csrf(b)}).status_code == 404
    assert a.post(f"/bookings/photos/{pid}/delete", headers={"x-csrf-token": csrf(a)}).json() == {"status": "ok"}


def test_visitor_photo_limits_still_apply(setup):
    v = visitor()
    for i in range(10):
        assert up(v, png(color=(i * 20, 9, 9))).status_code == 200
    r = up(v, png())
    assert r.status_code == 400 and "Maximum 10" in r.json()["detail"]


def test_anonymous_uploads_are_throttled_per_ip(monkeypatch):
    from app.bookings import photos

    monkeypatch.setattr(photos.anon_ip_limiter, "max_hits", 2)
    for _ in range(2):  # deux visiteuses différentes, même adresse IP
        assert up(visitor(), png()).status_code == 200
    r = up(visitor(), png())
    assert r.status_code == 400 and "Trop de photos" in r.json()["detail"]


def test_public_calendar_is_throttled_per_ip(setup, monkeypatch):
    monkeypatch.setattr(browse_limiter, "max_hits", 3)
    v = visitor()
    q = f"/bookings/calendar?service_id={setup['catalog']}"
    assert [v.get(q).status_code for _ in range(3)] == [200, 200, 200]
    assert v.get(q).status_code == 429


# --- téléphone du salon (solution de repli) -----------------------------------------------------------------------------


def test_salon_phone_setting_is_shown_when_set_and_hidden_otherwise(setup):
    admin = visitor()
    make_admin(admin)
    base = {"csrf_token": csrf(admin), "video_max_mb": "100", "video_max_seconds": "180", "calendar_step_min": "15",
            "booking_horizon_days": "60", "booking_min_notice_min": "60"}  # fmt: skip
    v = visitor()
    assert "Réservation par téléphone" not in v.get("/").text and "Appelez" not in html.unescape(v.get("/auth/login").text)
    assert admin.post("/admin/settings", data={**base, "salon_phone": "abc"}).status_code == 400  # invalide
    assert admin.post("/admin/settings", data={**base, "salon_phone": "514 555-0123"}).status_code == 303
    assert "514 555-0123" in v.get("/").text and "Réservation par téléphone" in v.get("/").text  # pied de page
    assert "514 555-0123" in v.get("/auth/login").text
    _, wall = review(v, setup)
    assert "514 555-0123" in wall.text and "réserver par téléphone" in html.unescape(wall.text)
    assert admin.post("/admin/settings", data={**base, "salon_phone": ""}).status_code == 303
    assert "514 555-0123" not in v.get("/").text  # vide : rien n'est affiché


# --- réservation saisie par le salon (téléphone) --------------------------------------------------------------------------


def admin_form(admin, setup, **over):
    data = {"csrf_token": csrf(admin), "full_name": "Julie Roy", "phone": "514 555-0199", "email": "",
            "service_id": setup["catalog"], "hairdresser": "any", "day": next_weekday(0).isoformat(),
            "time": "10:00", "notes": "Cliente par téléphone"}  # fmt: skip
    data.update(over)
    return admin.post("/admin/appointments/new", data=data)


def test_admin_creates_a_phone_booking_for_a_client_without_account(setup):
    admin = visitor()
    make_admin(admin)
    assert "Nouveau rendez-vous" in admin.get("/admin/appointments/new").text
    r = admin_form(admin, setup)
    assert r.status_code == 303 and "created=" in r.headers["location"]
    with SessionLocal() as db:
        appt = db.scalar(select(Appointment))
        client = db.get(User, appt.client_id)
        assert client.full_name == "Julie Roy" and client.phone == "514 555-0199" and client.role == Role.client
        assert client.email.startswith("tel-") and client.email.endswith("@sans-courriel.kyja")
        assert appt.computed_duration_min == 60 and appt.client_notes == "Cliente par téléphone"
    assert "Julie Roy" in admin.get("/admin/appointments").text
    # ce compte n'est pas utilisable pour se connecter : aucun mot de passe connu
    login_try = visitor()
    r = login_try.post("/auth/login", data={"email": client.email, "password": "n'importe quoi", "csrf_token": csrf(login_try)})
    assert r.status_code == 401


def test_admin_booking_reuses_an_existing_account_by_email(setup):
    admin = visitor()
    make_admin(admin)
    with SessionLocal() as db:
        existing = make_client_user(db, "deja@test.kyja")
    r = admin_form(admin, setup, full_name="", phone="", email="deja@test.kyja")
    assert r.status_code == 303
    with SessionLocal() as db:
        assert db.scalar(select(Appointment)).client_id == existing.id
        clients = db.scalars(select(User).where(User.role == Role.client, User.email.like("%@test.kyja"))).all()
        assert [c.email for c in clients] == ["deja@test.kyja"]  # une seule fiche cliente : pas de doublon


def test_admin_booking_validates_and_detects_conflicts(setup):
    admin = visitor()
    make_admin(admin)
    assert "nom de la cliente" in admin_form(admin, setup, full_name="").text
    assert "téléphone ou un email" in admin_form(admin, setup, phone="", email="").text
    assert "Heure invalide" in admin_form(admin, setup, time="25:99").text
    assert "Date invalide" in admin_form(admin, setup, day="pas-une-date").text
    assert "Téléphone invalide" in admin_form(admin, setup, phone="abc").text
    assert admin_form(admin, setup, time="10:00").status_code == 303
    r = admin_form(admin, setup, full_name="Autre Cliente", phone="514 555-0100", time="10:30")  # chevauche le précédent
    assert r.status_code == 400 and "vient d'être réservé" in html.unescape(r.text)
    with SessionLocal() as db:
        assert len(db.scalars(select(Appointment)).all()) == 1
        assert db.scalars(select(User).where(User.full_name == "Autre Cliente")).all() == []  # pas de fiche orpheline


def test_client_cannot_use_admin_phone_booking(setup):
    c = visitor()
    register(c)
    assert c.get("/admin/appointments/new").status_code == 403
    assert admin_form(c, setup).status_code == 403


def test_admin_override_skips_the_minimum_notice_only(setup):
    with SessionLocal() as db:
        h, svc = db.get(Hairdresser, setup["h"]), db.get(Service, setup["catalog"])
        client = make_client_user(db, "urgente@test.kyja")
        start = local_start(next_weekday(0), 10)
        now = start - timedelta(minutes=10)  # 10 minutes avant : sous le délai minimal de 60 min
        with pytest.raises(SlotUnavailable):
            engine.create_booking(db, client=client, service=svc, hairdresser_id=h.id, start=start, now=now)
        appt = engine.create_booking(db, client=client, service=svc, hairdresser_id=h.id, start=start, now=now, admin_override=True)
        assert appt.starts_at == start
        with pytest.raises(BookingError):  # les conflits restent contrôlés
            engine.create_booking(db, client=client, service=svc, hairdresser_id=h.id, start=start, now=now, admin_override=True)
