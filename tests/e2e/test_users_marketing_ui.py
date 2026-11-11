"""Utilisateurs, promotions, désabonnement et statuts actif / désactivé, dans un vrai navigateur."""
import re

from playwright.sync_api import expect
from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models.core import ConsentRecord, MessageOutbox, User
from app.db.models.settings import set_setting
from tests.conftest import make_category, make_service
from tests.e2e.test_booking_flow import ADMIN, CLIENT, salon, sign_in  # noqa: F401  (fixture `salon`)


def login_admin(page, next_url):
    page.goto(f"/auth/login?next={next_url}")
    sign_in(page, ADMIN)


def test_admin_home_has_the_new_tiles(page, shot, salon):
    login_admin(page, "/admin")
    for label in ("Utilisateurs", "Promotions", "Messages envoyés"):
        expect(page.locator(".admin-links").get_by_role("heading", name=label)).to_be_visible()
    shot("77-admin-tuiles")


def test_users_list_filters_by_typing_in_the_column(page, shot, salon):
    with SessionLocal() as db:
        client = db.scalar(select(User).where(User.email == CLIENT[0]))
        db.add(ConsentRecord(user_id=client.id, kind="marketing", granted=True))
        db.commit()
    login_admin(page, "/admin/users")
    rows = page.locator("table.admin-table tbody tr")
    expect(page.locator("table.admin-table")).to_contain_text(CLIENT[0])
    page.fill("tr.filter-row input[name=promo]", "accepte")
    page.press("tr.filter-row input[name=promo]", "Enter")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("Cliente E2E")
    expect(rows.first).to_contain_text("Accepte")
    shot("78-utilisateurs")


def test_admin_composes_reviews_tests_and_sends_a_promotion_then_client_unsubscribes(browser, base_url, page, shot, salon):
    with SessionLocal() as db:
        client = db.scalar(select(User).where(User.email == CLIENT[0]))
        db.add(ConsentRecord(user_id=client.id, kind="marketing", granted=True))
        set_setting(db, "salon_address", "123, rue Exemple, Montréal (Québec) H0H 0H0")
    login_admin(page, "/admin/marketing/new")
    page.fill("input[name=subject]", "Promo de novembre")
    page.fill("textarea[name=body]", "Profitez de -15 % sur toutes les tresses ce mois-ci.")
    page.click("text=Continuer : voir le résumé")
    expect(page.locator("h1")).to_have_text("Promo de novembre")
    expect(page.locator("button", has_text="Envoyer à 1 personne")).to_be_enabled()
    shot("79-promotion-resume")
    page.click("text=M'envoyer un essai par courriel")
    expect(page.locator("[role=status]")).to_contain_text("Essai envoyé")
    page.click("button:has-text('Envoyer à 1 personne')")
    expect(page.locator("body")).to_contain_text("Envoyée")
    with SessionLocal() as db:
        mail = db.scalar(select(MessageOutbox).where(MessageOutbox.to_address == CLIENT[0], MessageOutbox.campaign_id.is_not(None)))
    link = re.search(r"http://localhost:8000(/unsubscribe/[\w.\-]+)", mail.body).group(1)

    visitor = browser.new_context(base_url=base_url).new_page()  # la cliente, sans aucune session
    visitor.goto(link)
    expect(visitor.locator("h1")).to_have_text("Se désabonner des promotions")
    visitor.click("text=Confirmer le désabonnement")
    expect(visitor.locator("[role=status]")).to_contain_text("Vous ne recevrez plus")
    page.goto("/admin/marketing/new")
    expect(page.locator("body")).to_contain_text("0 personne")
    visitor.context.close()


def test_category_toggle_and_status_filter(page, shot, salon):
    with SessionLocal() as db:
        make_category(db, "TEST Catégorie à éteindre")
        make_service(db, "TEST Prestation d'essai")
    login_admin(page, "/admin/categories")
    row = page.locator("tbody tr").filter(has=page.locator("input[value='TEST Catégorie à éteindre']"))  # le nom est dans un champ
    expect(row).to_contain_text("Active")
    row.get_by_role("button", name="Désactiver").click()
    expect(row).to_contain_text("Désactivée")
    page.fill("tr.filter-row input[name=statut]", "désactivée")
    page.press("tr.filter-row input[name=statut]", "Enter")
    expect(page.locator("tbody tr")).to_have_count(1)
    shot("80-categories-statut")
