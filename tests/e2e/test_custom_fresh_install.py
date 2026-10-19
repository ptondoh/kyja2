"""Demande sur mesure sur une installation NEUVE : aucune prestation « sur devis » créée à la main."""
import re

import pytest
from PIL import Image
from playwright.sync_api import expect
from sqlalchemy import select

from app.auth.security import hash_password
from app.db.base import SessionLocal
from app.db.models.core import Appointment, Role, User
from tests.conftest import make_hairdresser, next_weekday
from tests.e2e.conftest import SHOTS, pick_day
from tests.e2e.test_booking_flow import CLIENT, sign_in

NOTES = "Tresses africaines avec rajouts, longueur milieu du dos, couleur noir et bordeaux"


@pytest.fixture
def fresh_salon():
    """Deux coiffeuses et une cliente. AUCUNE prestation créée : seule la demande intégrée existe."""
    with SessionLocal() as db:
        make_hairdresser(db, "Marie", "1.30")
        make_hairdresser(db, "Sonia", "1.00", slots=tuple((d, 540, 1020) for d in range(5)))
        db.add(User(email=CLIENT[0], password_hash=hash_password(CLIENT[1]), role=Role.client, full_name="Cliente E2E"))
        db.commit()


def test_custom_request_from_a_fresh_install(page, shot, fresh_salon, tmp_path):
    imgs = []
    for i in range(2):
        p = tmp_path / f"idee-{i}.png"
        Image.new("RGB", (500, 400), (40 + i * 90, 100, 160)).save(p)
        imgs.append(str(p))

    page.goto("/auth/login?next=/bookings/new")
    sign_in(page, CLIENT)
    expect(page.locator("h1")).to_have_text("Comment souhaitez-vous réserver ?")
    page.click("text=Demande sur mesure")

    # plus d'écran vide : on arrive directement sur le formulaire
    expect(page).to_have_url(re.compile(r"/bookings/new/\d+$"))
    expect(page.locator("h2", has_text="Votre demande")).to_be_visible()
    expect(page.locator("textarea[name=notes]")).to_be_visible()  # zone de texte pour la description
    expect(page.locator("#photo-zone")).to_be_visible()  # photos (10 maximum)
    expect(page.locator("#photo-count")).to_have_text("0 / 10 photos")
    expect(page.locator(".cal")).to_be_visible()  # vrai calendrier mensuel
    expect(page.locator(".cal-title")).to_be_visible()
    available = page.locator("label.day")
    assert available.count() > 0  # des jours sont réservables et mis en évidence
    assert page.locator(".day.is-off").count() > 0  # les autres sont grisés
    shot("40-sur-mesure-installation-neuve")

    # le choix de la coiffeuse met à jour le calendrier et la durée calculée
    expect(page.locator(".choice", has_text="Marie")).to_contain_text("3 h")  # 120 x 1,30 = 156 -> 165 (pas de 15) + 15 de repos = 180 min
    expect(page.locator(".choice", has_text="Sonia")).to_contain_text("2 h 15")  # 120 + 15

    # description + photos avec aperçu immédiat
    page.fill("textarea[name=notes]", NOTES)
    page.set_input_files("#photo-input", imgs)
    expect(page.locator("#photo-previews img")).to_have_count(2)
    expect(page.locator("#photo-previews .is-uploaded")).to_have_count(2, timeout=15000)

    # jour précis puis heure précise
    pick_day(page, next_weekday(0).isoformat())
    expect(page.locator("#slots .slot").first).to_be_visible()
    page.locator("#slots .slot", has_text=re.compile(r"^10 h$")).click()
    shot("41-sur-mesure-jour-heure")
    page.click("text=Voir le récapitulatif")

    recap = page.locator(".recap")
    expect(recap).to_contain_text("Demande sur mesure")
    expect(recap).to_contain_text("À confirmer au salon")
    expect(recap).to_contain_text("Tresses africaines")
    expect(recap).to_contain_text("Photos d'inspiration (2)")
    expect(recap).to_contain_text("10 h →")
    page.click("text=Confirmer la réservation")
    expect(page.locator("[role=status]")).to_contain_text("Rendez-vous confirmé")
    expect(page.locator(".recap .photo-grid img")).to_have_count(2)
    with SessionLocal() as db:
        appt = db.scalar(select(Appointment))
        assert appt.booking_mode.value == "custom" and appt.estimated_price is None and len(appt.photos) == 2
    assert page.console_errors == [], page.console_errors


def test_changing_hairdresser_refreshes_the_calendar(page, fresh_salon):
    with SessionLocal() as db:
        make_hairdresser(db, "Lundi", slots=((0, 540, 1020),), email="lundi@test.kyja")
    page.goto("/auth/login?next=/bookings/new?mode=custom")
    sign_in(page, CLIENT)
    expect(page.locator(".cal")).to_be_visible()
    before = page.locator("label.day").count()
    page.locator(".choice", has_text="Lundi").click()  # une coiffeuse qui ne travaille que le lundi
    page.wait_for_timeout(700)
    after = page.locator("label.day").count()
    assert 0 < after < before  # moins de jours qu'avec « peu importe »
    values = page.eval_on_selector_all("label.day input", "els => els.map(e => e.value)")
    from datetime import date

    assert all(date.fromisoformat(v).weekday() == 0 for v in values)


def test_month_navigation(page, fresh_salon):
    page.goto("/auth/login?next=/bookings/new?mode=custom")
    sign_in(page, CLIENT)
    title = page.locator(".cal-title")
    first = title.inner_text()
    expect(page.locator(".cal-nav.is-off").first).to_be_visible()  # pas de recul avant le mois courant
    page.click("button[aria-label='Mois suivant']")
    expect(title).not_to_have_text(first)
    assert page.locator("label.day").count() > 0
    page.click("button[aria-label='Mois précédent']")
    expect(title).to_have_text(first)
