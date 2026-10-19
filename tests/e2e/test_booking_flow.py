"""Parcours de réservation de bout en bout (navigateur réel + HTMX)."""
import re

import pytest
from playwright.sync_api import expect

from app.auth.security import hash_password
from app.db.base import SessionLocal
from app.db.models.core import PricingMode, Role, User
from tests.e2e.conftest import SHOTS, choose_hairdresser, pick_day
from tests.conftest import make_hairdresser, make_service, next_weekday

CLIENT = ("cliente-e2e@test.kyja", "client password 12")
ADMIN = ("booking-admin@test.kyja", "admin password 123")


@pytest.fixture
def salon():
    with SessionLocal() as db:
        make_hairdresser(db, "Marie", "1.30")
        make_hairdresser(db, "Sonia", "1.00")
        make_service(db, "TEST Brushing", base=60, buffer=10, price="55.00")
        make_service(db, "TEST Tresses", base=120, mode=PricingMode.quote)
        for (email, pwd), role, name in ((CLIENT, Role.client, "Cliente E2E"), (ADMIN, Role.admin, "Admin E2E")):
            db.add(User(email=email, password_hash=hash_password(pwd), role=role, full_name=name))
        db.commit()


def sign_in(page, creds):
    page.fill("input[name=email]", creds[0])
    page.fill("input[name=password]", creds[1])
    page.click("button[type=submit]")


def test_catalog_booking_flow_with_login_redirect(page, shot, salon):
    day = next_weekday(0).isoformat()

    # 1. choix du mode puis de la coiffure
    page.goto("/")
    page.click("header >> text=Réserver")
    expect(page.locator("h1")).to_have_text("Comment souhaitez-vous réserver ?")
    shot("20-reservation-mode")
    page.click("text=Choisir une coiffure")
    page.locator(".tile", has_text="TEST Brushing").click()

    # 2. non connectée : redirection vers la connexion, puis retour automatique sur la page demandée
    expect(page).to_have_url(re.compile(r"/auth/login\?next="))
    sign_in(page, CLIENT)
    expect(page).to_have_url(re.compile(r"/bookings/new/\d+$"))

    # 3. la durée calculée est affichée par coiffeuse
    marie = page.locator(".choice", has_text="Marie")
    sonia = page.locator(".choice", has_text="Sonia")
    expect(marie).to_contain_text("1 h 40")
    expect(sonia).to_contain_text("1 h 10")

    # 4. les créneaux se chargent dynamiquement (HTMX) et dépendent de la coiffeuse choisie
    choose_hairdresser(page, marie)
    pick_day(page, day)
    expect(page.locator("#slots .slot").first).to_be_visible()
    expect(page.locator("#slots")).to_contain_text("Durée avec cette coiffeuse : 1 h 40")
    expect(page.locator("#slots .slot", has_text="15 h 30")).to_have_count(0)  # fin à 17 h 10 : trop tard
    shot("21-reservation-creneaux-marie")

    choose_hairdresser(page, sonia)  # changer de coiffeuse recharge le calendrier sans rechargement de page
    expect(page.locator("#slots")).to_contain_text("Choisissez un jour dans le calendrier")  # choix du jour remis à zéro
    pick_day(page, day)
    expect(page.locator("#slots")).to_contain_text("Durée avec cette coiffeuse : 1 h 10")
    expect(page.locator("#slots .slot", has_text="15 h 30")).to_have_count(1)

    choose_hairdresser(page, marie)
    pick_day(page, day)
    expect(page.locator("#slots")).to_contain_text("1 h 40")
    page.locator("#slots .slot", has_text=re.compile(r"^10 h$")).click()
    shot("22-reservation-choix")
    page.click("text=Voir le récapitulatif")

    # 5. récapitulatif : durée calculée, prix estimatif, paiement au salon
    expect(page.locator("h1")).to_have_text("Récapitulatif")
    recap = page.locator(".recap")
    expect(recap).to_contain_text("Marie")
    expect(recap).to_contain_text("10 h → 11 h 40")
    expect(recap).to_contain_text("1 h 40")
    expect(recap).to_contain_text("55.00 $ CAD")
    expect(recap).to_contain_text("estimation")
    expect(recap).to_contain_text("Au salon (Cash / Interac)")
    shot("23-reservation-recap")

    # 6. confirmation
    page.click("text=Confirmer la réservation")
    expect(page.locator("[role=status]")).to_contain_text("Rendez-vous confirmé")
    shot("24-reservation-confirmee")
    page.click("text=Mes rendez-vous >> nth=0")
    expect(page.locator("li", has_text="TEST Brushing")).to_be_visible()
    shot("25-mes-rendez-vous")

    # 7. annulation
    page.click("li >> text=TEST Brushing")
    page.click("text=Annuler ce rendez-vous")
    expect(page.locator("[role=status]")).to_contain_text("Rendez-vous annulé")
    assert page.console_errors == [], page.console_errors  # aucune violation CSP (HTMX inclus)


def test_custom_booking_requires_description_and_shows_no_price(page, shot, salon):
    day = next_weekday(0).isoformat()
    page.goto("/auth/login?next=/bookings/new?mode=custom")
    sign_in(page, CLIENT)
    page.locator(".tile", has_text="TEST Tresses").click()
    page.locator(".choice", has_text="Peu importe").click()
    pick_day(page, day)
    expect(page.locator("#slots .slot").first).to_be_visible()
    expect(page.locator("#slots")).to_contain_text("dépend de la coiffeuse")
    page.locator("#slots .slot").first.click()

    # description obligatoire en mode « sur mesure » (validation du navigateur)
    assert page.eval_on_selector("textarea[name=notes]", "e => e.required") is True
    page.click("text=Voir le récapitulatif")
    expect(page).to_have_url(re.compile(r"/bookings/new/\d+$"))  # bloqué : on reste sur la page

    page.fill("textarea[name=notes]", "Tresses africaines avec rajouts, longueur milieu du dos")
    shot("26-sur-mesure")
    page.click("text=Voir le récapitulatif")
    recap = page.locator(".recap")
    expect(recap).to_contain_text("À confirmer au salon")
    expect(recap).to_contain_text("Tresses africaines")
    expect(recap).to_contain_text("assignée automatiquement")
    expect(recap).to_contain_text("2 h")  # 120 min x coefficient (1,0 ou 1,3 selon la coiffeuse assignée)
    page.click("text=Confirmer la réservation")
    expect(page.locator("[role=status]")).to_contain_text("Rendez-vous confirmé")
    assert page.console_errors == []


def test_two_clients_cannot_take_the_same_slot(browser, base_url, salon):
    day = next_weekday(0).isoformat()
    with SessionLocal() as db:
        db.add(User(email="seconde@test.kyja", password_hash=hash_password("client password 12"), role=Role.client, full_name="Seconde"))
        db.commit()

    def open_pick(creds):
        ctx = browser.new_context(base_url=base_url)
        pg = ctx.new_page()
        pg.goto("/auth/login?next=/bookings/new?mode=catalog")
        sign_in(pg, creds)
        pg.locator(".tile", has_text="TEST Brushing").click()
        choose_hairdresser(pg, pg.locator(".choice", has_text="Marie"))
        pick_day(pg, day)
        expect(pg.locator("#slots .slot").first).to_be_visible()
        pg.locator("#slots .slot", has_text=re.compile(r"^14 h$")).click()
        pg.click("text=Voir le récapitulatif")
        expect(pg.locator("h1")).to_have_text("Récapitulatif")
        return ctx, pg

    ctx1, p1 = open_pick(CLIENT)
    ctx2, p2 = open_pick(("seconde@test.kyja", "client password 12"))  # les deux voient le même créneau libre
    p1.click("text=Confirmer la réservation")
    expect(p1.locator("[role=status]")).to_contain_text("Rendez-vous confirmé")
    p2.click("text=Confirmer la réservation")  # trop tard : créneau pris entre-temps
    expect(p2.locator(".error")).to_contain_text("vient d'être réservé")
    expect(p2.locator(".cal")).to_be_visible()  # retour au choix de créneau
    ctx1.close()
    ctx2.close()


def test_admin_sets_final_price_and_client_sees_it(browser, base_url, page, salon):
    day = next_weekday(0).isoformat()
    page.goto("/auth/login?next=/bookings/new?mode=catalog")
    sign_in(page, CLIENT)
    page.locator(".tile", has_text="TEST Brushing").click()
    page.locator(".choice", has_text="Peu importe").click()
    pick_day(page, day)
    expect(page.locator("#slots .slot").first).to_be_visible()
    page.locator("#slots .slot", has_text=re.compile(r"^11 h$")).click()
    page.click("text=Voir le récapitulatif")
    page.click("text=Confirmer la réservation")
    detail_url = page.url.split("?")[0]

    ctx = browser.new_context(base_url=base_url)
    admin = ctx.new_page()
    admin.goto("/auth/login?next=/admin/appointments")
    sign_in(admin, ADMIN)
    row = admin.locator("tr", has_text="TEST Brushing")
    expect(row).to_contain_text("55.00 $ CAD")
    row.locator("input[name=price]").fill("62.50")
    row.locator("text=Terminer").click()
    expect(admin.locator("tr", has_text="TEST Brushing")).to_contain_text("62.50 $ CAD")
    admin.screenshot(path=str(SHOTS / "27-admin-rdv.png"), full_page=True)
    ctx.close()

    page.goto(detail_url)
    expect(page.locator(".recap")).to_contain_text("62.50 $ CAD")
    expect(page.locator(".recap")).to_contain_text("montant final")
    expect(page.locator(".status")).to_have_text("Terminé")
