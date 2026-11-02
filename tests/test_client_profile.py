"""Profil de la cliente, export de ses données et droit à l'oubli (LPRPDE)."""
import html
import json
import logging
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.bookings import engine
from app.clients import service
from app.clients.router import sensitive_limiter
from app.config import get_settings
from app.db.base import SessionLocal
from app.db.models.core import (
    Appointment,
    AppointmentStatus,
    BookingPhoto,
    ConsentRecord,
    FinancialRecord,
    RefreshToken,
    Role,
    User,
)
from app.main import app
from tests.conftest import csrf, local_start, login, make_admin, make_hairdresser, make_service, next_weekday, register
from tests.test_booking_photos import png, up

PASSWORD = "correct horse battery"
NOTES = "Tresses africaines avec rajouts, longueur milieu du dos, couleur noir et bordeaux"


@pytest.fixture
def setup():
    with SessionLocal() as db:
        h = make_hairdresser(db, "Marie", "1.00", slots=tuple((d, 540, 1020) for d in range(7)))
        catalog = make_service(db, "TEST Brushing", base=60, price="55.00")
        return {"h": h.id, "catalog": catalog.id, "custom": engine.custom_request_service(db).id}


def visitor():
    c = TestClient(app, follow_redirects=False)
    c.get("/auth/login")
    return c


def client_account(email="a@test.kyja", **extra):
    c = visitor()
    register(c, email=email, **extra)
    return c


def uid(email="a@test.kyja") -> int:
    with SessionLocal() as db:
        return db.scalar(select(User.id).where(User.email == email))


def user_row(email_or_id):
    with SessionLocal() as db:
        if isinstance(email_or_id, int):
            return db.get(User, email_or_id)
        return db.scalar(select(User).where(User.email == email_or_id))


def post(c, path, **data):
    return c.post(path, data={"csrf_token": csrf(c), **data})


def book(c, setup, key="catalog", day_offset=0, hour=10, notes="", photo_ids=()):
    day = next_weekday(0) + timedelta(days=day_offset)
    r = c.post("/bookings", data={"service_id": setup[key], "hairdresser": str(setup["h"]), "start": local_start(day, hour).isoformat(),
                                  "notes": notes, "photo_ids": list(photo_ids), "csrf_token": csrf(c)})  # fmt: skip
    assert r.status_code == 303, r.text[:300]
    return int(re.search(r"/bookings/(\d+)", r.headers["location"]).group(1))


def make_past(appt_id: int):
    with SessionLocal() as db:
        a = db.get(Appointment, appt_id)
        a.starts_at, a.ends_at = datetime.now(timezone.utc) - timedelta(days=3), datetime.now(timezone.utc) - timedelta(days=3) + timedelta(hours=1)
        db.commit()


# --- accès et affichage ----------------------------------------------------------------------------------------


def test_profile_requires_login(client):
    r = client.get("/clients/me")
    assert r.status_code == 303 and r.headers["location"] == "/auth/login?next=/clients/me"
    assert client.get("/clients/me/export").status_code == 303


def test_profile_page_shows_account_and_header_link():
    c = client_account(marketing_optin="true")
    page = c.get("/clients/me").text
    assert "a@test.kyja" in page and 'value="Test User"' in page and "Mon profil" in page
    assert 'name="marketing_optin" value="true" checked' in page
    assert 'href="/clients/me"' in c.get("/").text  # le nom dans l'en-tête mène au profil


def test_marketing_consent_is_unchecked_by_default():
    c = client_account()
    assert "checked" not in re.search(r'<input type="checkbox" name="marketing_optin"[^>]*>', c.get("/clients/me").text).group(0)


# --- informations personnelles -------------------------------------------------------------------------------------


def test_update_profile_and_validation():
    c = client_account()
    r = post(c, "/clients/me", full_name="Julie Roy", phone="514 555-0199", address="123 rue Principale, Montréal")
    assert r.status_code == 303 and r.headers["location"].endswith("saved=profile")
    u = user_row("a@test.kyja")
    assert (u.full_name, u.phone, u.address) == ("Julie Roy", "514 555-0199", "123 rue Principale, Montréal")
    assert "Vos informations sont enregistrées" in c.get(r.headers["location"]).text
    for bad in ({"full_name": ""}, {"full_name": "x" * 201}, {"phone": "abc"}, {"address": "x" * 501}):
        data = {"full_name": "Julie Roy", "phone": "", "address": "", **bad}
        assert post(c, "/clients/me", **data).status_code == 400
    assert user_row("a@test.kyja").full_name == "Julie Roy"  # rien n'a bougé
    post(c, "/clients/me", full_name="Julie Roy", phone="", address="")
    assert user_row("a@test.kyja").phone is None  # vider un champ l'efface


def test_profile_escapes_html():
    c = client_account()
    post(c, "/clients/me", full_name="<script>alert(1)</script>", phone="", address="")
    page = c.get("/clients/me").text
    assert "<script>alert(1)</script>" not in page and "&lt;script&gt;" in page


def test_profile_post_requires_csrf():
    c = client_account()
    assert c.post("/clients/me", data={"full_name": "Pirate"}).status_code == 403
    assert user_row("a@test.kyja").full_name == "Test User"


# --- consentement (historique conservé) --------------------------------------------------------------------------------


def test_consent_changes_append_history_and_never_rewrite_it():
    c = client_account()
    with SessionLocal() as db:
        before = [(x.kind, x.granted) for x in db.scalars(select(ConsentRecord).order_by(ConsentRecord.id))]
    assert before == [("terms", True), ("marketing", False)]
    post(c, "/clients/me/consent", marketing_optin="true")  # j'accepte
    post(c, "/clients/me/consent", marketing_optin="true")  # inchangé : pas de doublon
    post(c, "/clients/me/consent")  # je retire
    with SessionLocal() as db:
        rows = [(x.kind, x.granted) for x in db.scalars(select(ConsentRecord).order_by(ConsentRecord.id))]
        assert rows == [("terms", True), ("marketing", False), ("marketing", True), ("marketing", False)]
        assert service.marketing_state(db, uid()).granted is False
    assert "Dernier choix enregistré" in c.get("/clients/me").text


# --- mot de passe ---------------------------------------------------------------------------------------------------------


def test_change_password_logs_out_other_devices_but_not_this_one():
    me = client_account()
    other = visitor()
    login(other)
    r = post(me, "/clients/me/password", current_password=PASSWORD, new_password="un tout nouveau mot de passe")
    assert r.status_code == 303 and r.headers["location"].endswith("saved=password")
    assert me.get("/clients/me").status_code == 200  # cet appareil reste connecté
    other.cookies.delete("access_token")
    assert other.get("/auth/me").status_code == 401  # l'autre appareil ne peut plus se renouveler
    fresh = visitor()
    assert login(fresh).status_code == 401  # l'ancien mot de passe ne marche plus
    assert fresh.post("/auth/login", data={"email": "a@test.kyja", "password": "un tout nouveau mot de passe", "csrf_token": csrf(fresh)}).status_code == 303
    assert user_row("a@test.kyja").password_hash.startswith("$argon2id$")


def test_change_password_validation_and_wrong_current():
    c = client_account()
    r = post(c, "/clients/me/password", current_password="mauvais", new_password="un tout nouveau mot de passe")
    assert r.status_code == 400 and "incorrect" in html.unescape(r.text)
    assert post(c, "/clients/me/password", current_password=PASSWORD, new_password="court").status_code == 400
    assert post(c, "/clients/me/password", current_password=PASSWORD, new_password="x" * 129).status_code == 400
    assert login(visitor()).status_code == 303  # le mot de passe n'a pas changé


def test_password_checks_are_rate_limited():
    c = client_account()
    for _ in range(5):
        assert post(c, "/clients/me/password", current_password="mauvais", new_password="un tout nouveau mot de passe").status_code == 400
    # même avec le BON mot de passe, le compte est temporairement bloqué : pas de force brute possible
    r = post(c, "/clients/me/password", current_password=PASSWORD, new_password="un tout nouveau mot de passe")
    assert r.status_code == 400 and "trop d'essais" in html.unescape(r.text)
    # la page de suppression partage la même protection
    assert post(c, "/clients/me/delete", password=PASSWORD, confirmation="SUPPRIMER").status_code == 400
    assert user_row("a@test.kyja").anonymized_at is None


# --- export ---------------------------------------------------------------------------------------------------------------------


def test_export_contains_everything_and_no_secrets(setup):
    c = client_account(marketing_optin="true")
    post(c, "/clients/me", full_name="Julie Roy", phone="514 555-0199", address="123 rue Principale")
    pid = up(c, png()).json()["id"]
    appt = book(c, setup, key="custom", notes=NOTES, photo_ids=[pid])
    r = c.get("/clients/me/export")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert r.headers["content-disposition"].startswith('attachment; filename="kyja-mes-donnees-') and r.headers["cache-control"] == "no-store"
    data = json.loads(r.content)
    assert data["profile"] == {"id": uid(), "full_name": "Julie Roy", "email": "a@test.kyja", "phone": "514 555-0199",
                               "address": "123 rue Principale", "created_at": data["profile"]["created_at"]}  # fmt: skip
    assert [(x["kind"], x["granted"]) for x in data["consents"]] == [("terms", True), ("marketing", True)]
    [a] = data["appointments"]
    assert a["id"] == appt and a["service"] == "Demande sur mesure" and a["your_description"] == NOTES and a["photo_ids"] == [pid]
    assert a["hairdresser"] == "Marie" and a["duration_minutes"] == 135 and a["estimated_price"] is None
    assert data["photos"][0]["id"] == pid and data["photos"][0]["appointment_id"] == appt
    text = r.text.lower()
    assert "$argon2" not in text and "password" not in text and "token" not in text  # aucun secret


def test_export_only_contains_my_own_data(setup):
    me, other = client_account("a@test.kyja"), client_account("b@test.kyja")
    post(other, "/clients/me", full_name="Autre Personne", phone="", address="")
    book(other, setup, hour=11)
    data = json.loads(me.get("/clients/me/export").content)
    assert data["appointments"] == [] and "Autre Personne" not in json.dumps(data) and "b@test.kyja" not in json.dumps(data)


# --- droit à l'oubli -------------------------------------------------------------------------------------------------------------


def full_account(setup):
    """Une cliente avec tout ce qui compte : rendez-vous passé payé, rendez-vous à venir sur mesure avec photos."""
    c = client_account(marketing_optin="true")
    post(c, "/clients/me", full_name="Julie Roy", phone="514 555-0199", address="123 rue Principale")
    past_id = book(c, setup, key="catalog", day_offset=0, hour=10, notes="note passée")
    make_past(past_id)
    with SessionLocal() as db:
        engine.set_final_price(db, db.get(Appointment, past_id), Decimal("62.50"))
        db.execute(update(Appointment).where(Appointment.id == past_id).values(client_notes="écrit par la cliente"))
        db.commit()
    pids = [up(c, png(color=(i * 40, 60, 90))).json()["id"] for i in range(2)]
    future_id = book(c, setup, key="custom", day_offset=7, hour=14, notes=NOTES, photo_ids=pids)
    return c, past_id, future_id, pids


def test_forget_requires_password_and_confirmation_word(setup):
    c = client_account()
    assert post(c, "/clients/me/delete", password="mauvais", confirmation="SUPPRIMER").status_code == 400
    assert post(c, "/clients/me/delete", password=PASSWORD, confirmation="supprimer pas").status_code == 400
    assert post(c, "/clients/me/delete", password=PASSWORD, confirmation="").status_code == 400
    assert c.post("/clients/me/delete", data={"password": PASSWORD, "confirmation": "SUPPRIMER"}).status_code == 403  # sans CSRF
    u = user_row("a@test.kyja")
    assert u.anonymized_at is None and u.is_active and u.full_name == "Test User"  # rien n'a été effacé


def test_forget_anonymizes_identity_and_ends_sessions(setup):
    c, *_ = full_account(setup)
    refresh_before = c.cookies.get("refresh_token")
    r = post(c, "/clients/me/delete", password=PASSWORD, confirmation=" SUPPRIMER ")
    assert r.status_code == 303 and r.headers["location"] == "/clients/deleted"
    cookies = " ".join(r.headers.get_list("set-cookie")).lower()
    assert "access_token" in cookies and "refresh_token" in cookies and "max-age=0" in cookies  # cookies supprimés
    uid_after = _forgotten_id()
    u = user_row(uid_after)
    assert u.full_name == "CLIENT_SUPPRIMÉ" and u.email == f"anon_{uid_after}@supprime.kyja"
    assert u.phone is None and u.address is None and u.is_active is False and u.anonymized_at is not None
    assert u.email.isascii()  # l'adresse anonymisée est un email valide (domaine sans accent)
    # impossible de se reconnecter, ni avec l'ancien email, ni avec le nouveau
    for email in ("a@test.kyja", u.email):
        v = visitor()
        assert v.post("/auth/login", data={"email": email, "password": PASSWORD, "csrf_token": csrf(v)}).status_code == 401
    # l'ancienne session ne peut pas se renouveler (cookie refresh d'avant la suppression)
    old = visitor()
    old.cookies.set("refresh_token", refresh_before, path="/")
    assert old.get("/auth/me").status_code == 401
    with SessionLocal() as db:
        assert all(t.revoked_at is not None for t in db.scalars(select(RefreshToken).where(RefreshToken.user_id == uid_after)))
    assert visitor().get("/clients/deleted").status_code == 200  # page de confirmation publique
    assert c.get("/clients/me").status_code == 303  # et plus rien d'accessible


def _forgotten_id() -> int:
    with SessionLocal() as db:
        return db.scalar(select(User.id).where(User.anonymized_at.is_not(None)))


def test_forget_deletes_personal_content_and_keeps_legal_records(setup):
    c, past_id, future_id, pids = full_account(setup)
    private = Path(get_settings().private_dir)
    with SessionLocal() as db:
        keys = [p.storage_key for p in db.scalars(select(BookingPhoto))]
        consents_before = db.scalar(select(func.count()).select_from(ConsentRecord))
        finance_before = [(f.appointment_id, f.amount) for f in db.scalars(select(FinancialRecord))]
    assert len(keys) == 2 and all((private / k).exists() for k in keys) and finance_before == [(past_id, Decimal("62.50"))]

    post(c, "/clients/me/delete", password=PASSWORD, confirmation="SUPPRIMER")

    with SessionLocal() as db:
        # SUPPRIMÉ : photos (fichiers ET lignes), descriptions
        assert db.scalars(select(BookingPhoto)).all() == [] and not any((private / k).exists() for k in keys)
        assert all(a.client_notes is None for a in db.scalars(select(Appointment)))
        # ANNULÉ : le rendez-vous à venir (le créneau est libéré)
        assert db.get(Appointment, future_id).status == AppointmentStatus.cancelled
        # CONSERVÉ : rendez-vous passé (référence anonymisée), prix, enregistrement financier, preuves de consentement
        past = db.get(Appointment, past_id)
        assert past.status == AppointmentStatus.completed and past.final_price == Decimal("62.50") and past.client_id is not None
        assert [(f.appointment_id, f.amount) for f in db.scalars(select(FinancialRecord))] == finance_before
        assert db.scalar(select(func.count()).select_from(ConsentRecord)) == consents_before
        assert db.get(User, past.client_id).full_name == "CLIENT_SUPPRIMÉ"
    # le créneau annulé peut être pris par une autre cliente
    other = client_account("b@test.kyja")
    book(other, setup, key="custom", day_offset=7, hour=14, notes=NOTES)


def test_forget_leaves_other_clients_untouched(setup):
    c, *_ = full_account(setup)
    other = client_account("b@test.kyja")
    post(other, "/clients/me", full_name="Autre Cliente", phone="514 555-0100", address="")
    opid = up(other, png(color=(1, 2, 3))).json()["id"]
    oappt = book(other, setup, key="custom", day_offset=8, hour=11, notes=NOTES, photo_ids=[opid])
    post(c, "/clients/me/delete", password=PASSWORD, confirmation="SUPPRIMER")
    with SessionLocal() as db:
        ob = db.scalar(select(User).where(User.email == "b@test.kyja"))
        assert ob.full_name == "Autre Cliente" and ob.phone == "514 555-0100" and ob.is_active
        a = db.get(Appointment, oappt)
        assert a.status == AppointmentStatus.pending_quote and a.client_notes == NOTES and [p.id for p in a.photos] == [opid]
    assert other.get(f"/bookings/photos/{opid}").status_code == 200


def test_email_is_freed_after_forgetting(setup):
    c = client_account()
    post(c, "/clients/me/delete", password=PASSWORD, confirmation="SUPPRIMER")
    again = visitor()
    r = register(again, email="a@test.kyja")  # la même adresse peut recréer un compte tout neuf
    assert r.status_code == 303 and again.get("/clients/me").status_code == 200


def test_staff_accounts_cannot_use_self_service_deletion(setup):
    admin = visitor()
    make_admin(admin)
    r = post(admin, "/clients/me/delete", password="admin password 123", confirmation="SUPPRIMER")
    assert r.status_code == 403 and "contactez le salon" in html.unescape(r.text)
    assert "géré par le salon" in html.unescape(admin.get("/clients/me").text)  # pas de bouton de suppression pour le personnel
    assert 'action="/clients/me/delete"' not in admin.get("/clients/me").text
    assert user_row("admin@test.kyja").anonymized_at is None
    with SessionLocal() as db:
        h = make_hairdresser(db, "Sonia", email="sonia@test.kyja")  # mot de passe : « hairdresser password 1 »
    s = visitor()
    s.post("/auth/login", data={"email": "sonia@test.kyja", "password": "hairdresser password 1", "csrf_token": csrf(s)})
    assert post(s, "/clients/me/delete", password="hairdresser password 1", confirmation="SUPPRIMER").status_code == 403


def test_admin_sees_the_anonymized_name_on_past_appointments(setup):
    c, past_id, *_ = full_account(setup)
    post(c, "/clients/me/delete", password=PASSWORD, confirmation="SUPPRIMER")
    admin = visitor()
    make_admin(admin)
    page = admin.get("/admin/appointments").text
    assert "CLIENT_SUPPRIMÉ" in page and "Julie Roy" not in page and "a@test.kyja" not in page and "514 555-0199" not in page


def test_forget_is_audited_without_personal_data(setup, caplog):
    c, *_ = full_account(setup)
    with caplog.at_level(logging.INFO, logger="kyja.audit"):
        post(c, "/clients/me/delete", password=PASSWORD, confirmation="SUPPRIMER")
    line = next(r.getMessage() for r in caplog.records if "account_anonymized" in r.getMessage())
    assert re.search(r"user_id=\d+ photos_deleted=2 future_appointments_cancelled=1", line)
    assert "Julie" not in line and "a@test.kyja" not in line  # la trace ne contient aucune donnée personnelle
