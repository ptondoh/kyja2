"""Demande sur mesure et réservation par téléphone, sans compte préalable (navigateur réel)."""
import re
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import expect
from sqlalchemy import select

from app.auth.security import hash_password
from app.db.base import SessionLocal
from app.db.models.core import Appointment, BookingPhoto, Role, User
from tests.conftest import make_hairdresser, next_weekday
from tests.e2e.conftest import SHOTS, pick_day
from tests.e2e.test_booking_flow import ADMIN, CLIENT, sign_in

NOTES = "Tresses africaines avec rajouts, longueur milieu du dos, couleur noir et bordeaux"


@pytest.fixture
def salon_no_account():
    """Deux coiffeuses, une cliente DÉJÀ inscrite (pour « j'ai déjà un compte ») et un admin."""
    with SessionLocal() as db:
        make_hairdresser(db, "Marie", "1.30", slots=tuple((d, 540, 1020) for d in range(5)))
        make_hairdresser(db, "Sonia", "1.00", slots=tuple((d, 540, 1020) for d in range(5)))
        for (email, pwd), role, name in ((CLIENT, Role.client, "Cliente E2E"), (ADMIN, Role.admin, "Admin E2E")):
            db.add(User(email=email, password_hash=hash_password(pwd), role=role, full_name=name))
        db.commit()


def test_visitor_custom_request_with_photos_then_login(page, shot, salon_no_account, tmp_path):
    imgs = []
    for i in range(2):
        p = tmp_path / f"idee-{i}.png"
        Image.new("RGB", (500, 400), (40 + i * 90, 100, 160)).save(p)
        imgs.append(str(p))

    # visiteuse : aucune connexion, directement le formulaire de la demande sur mesure
    page.goto("/bookings/new?mode=custom")
    expect(page).to_have_url(re.compile(r"/bookings/new/\d+$"))
    expect(page.locator("textarea[name=notes]")).to_be_visible()
    page.fill("textarea[name=notes]", NOTES)
    page.set_input_files("#photo-input", imgs)  # photos AVANT toute connexion
    expect(page.locator("#photo-previews img")).to_have_count(2)
    expect(page.locator("#photo-previews .is-uploaded")).to_have_count(2, timeout=15000)
    pick_day(page, next_weekday(0).isoformat())
    expect(page.locator("#slots .slot").first).to_be_visible()
    page.locator("#slots .slot", has_text=re.compile(r"^10 h$")).click()
    page.click("text=Voir le récapitulatif")

    wall = page.locator("#login-wall")
    expect(wall).to_contain_text("vos photos")  # le message précise que rien n'est perdu
    expect(page.locator(".recap")).to_contain_text("Photos d'inspiration (2)")
    shot("42-visiteuse-recap-photos")

    # « J'ai déjà un compte » : connexion, puis retour avec description et photos conservées
    page.click("#login-wall >> text=J'ai déjà un compte")
    sign_in(page, CLIENT)
    expect(page).to_have_url(re.compile(r"/bookings/resume$"))
    expect(page.locator(".recap")).to_contain_text("Tresses africaines")
    expect(page.locator(".recap")).to_contain_text("Photos d'inspiration (2)")
    expect(page.locator(".recap .photo-grid img")).to_have_count(2)
    page.click("text=Confirmer la réservation")
    expect(page.locator("[role=status]")).to_contain_text("Rendez-vous confirmé")
    expect(page.locator(".recap .photo-grid img")).to_have_count(2)

    with SessionLocal() as db:
        appt = db.scalar(select(Appointment))
        assert appt.booking_mode.value == "custom" and len(appt.photos) == 2
        owner = db.get(User, appt.client_id)
        assert owner.email == CLIENT[0]  # les photos envoyées sans compte appartiennent maintenant à la cliente
        assert all(p.client_id == owner.id and p.draft_token_hash is None for p in db.scalars(select(BookingPhoto)))
    assert page.console_errors == [], page.console_errors


def test_visitor_session_cookie_is_scoped_to_bookings(page, salon_no_account):
    page.goto("/bookings/new?mode=custom")
    page.set_input_files("#photo-input", str(Path(__file__).parents[2] / "app" / "static" / "img" / "favicon.png"))
    expect(page.locator("#photo-previews .is-uploaded")).to_have_count(1, timeout=15000)
    cookies = {c["name"]: c for c in page.context.cookies()}
    draft = cookies["booking_draft"]
    assert draft["httpOnly"] and draft["sameSite"] == "Lax" and draft["path"] == "/bookings"
    assert "booking_draft" not in page.evaluate("document.cookie")  # illisible par JavaScript


def test_admin_creates_a_phone_booking(page, shot, salon_no_account):
    page.goto("/auth/login?next=/admin/appointments")
    sign_in(page, ADMIN)
    page.click("text=Nouveau rendez-vous (téléphone)")
    page.fill("input[name=full_name]", "Julie Roy")
    page.fill("input[name=phone]", "514 555-0199")
    page.locator("select[name=service_id]").select_option(index=0)
    page.fill("input[name=day]", next_weekday(0).isoformat())
    page.fill("input[name=time]", "10:00")
    shot("43-admin-rdv-telephone")
    page.click("text=Créer le rendez-vous")
    expect(page.locator("[role=status]")).to_contain_text("Rendez-vous créé")
    expect(page.locator("tr", has_text="Julie Roy")).to_contain_text("Demande sur mesure")
