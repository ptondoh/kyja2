"""Profil, export et droit à l'oubli dans un vrai navigateur."""
import json
import re

from playwright.sync_api import expect
from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models.core import ConsentRecord, User
from tests.e2e.conftest import SHOTS

EMAIL, PASSWORD = "profil-e2e@test.kyja", "un mot de passe solide"


def sign_up(page):
    page.goto("/auth/register")
    page.fill("input[name=full_name]", "Julie Roy")
    page.fill("input[name=email]", EMAIL)
    page.fill("input[name=password]", PASSWORD)
    page.check("input[name=accept_terms]")
    page.click("button[type=submit]")
    expect(page.locator("header").first).to_contain_text("Julie Roy")


def open_profile(page):
    page.click("header >> a[title='Mon profil']")
    expect(page.locator("h1")).to_have_text("Mon profil")


def test_edit_profile_and_consent(page, shot):
    sign_up(page)
    open_profile(page)
    shot("50-profil")
    page.fill("input[name=phone]", "514 555-0199")
    page.fill("textarea[name=address]", "123 rue Principale, Montréal")
    page.locator("section", has=page.locator("h2", has_text="Mes informations")).locator("button[type=submit]").click()
    expect(page.locator("[role=status]")).to_contain_text("Vos informations sont enregistrées")
    expect(page.locator("input[name=phone]")).to_have_value("514 555-0199")

    box = page.locator("input[name=marketing_optin]")
    expect(box).not_to_be_checked()  # jamais coché par défaut
    box.check()
    page.click("text=Enregistrer mon choix")
    expect(page.locator("[role=status]")).to_contain_text("choix de communications")
    expect(page.locator("input[name=marketing_optin]")).to_be_checked()
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.email == EMAIL))
        kinds = [(c.kind, c.granted) for c in db.scalars(select(ConsentRecord).where(ConsentRecord.user_id == uid).order_by(ConsentRecord.id))]
    assert kinds == [("terms", True), ("marketing", False), ("marketing", True)]  # l'historique s'allonge
    assert page.console_errors == [], page.console_errors


def test_download_my_data(page):
    sign_up(page)
    open_profile(page)
    with page.expect_download() as info:
        page.click("text=Télécharger mes données")
    download = info.value
    assert re.fullmatch(r"kyja-mes-donnees-\d{8}\.json", download.suggested_filename)
    data = json.loads(open(download.path(), encoding="utf-8").read())
    assert data["profile"]["email"] == EMAIL and data["profile"]["full_name"] == "Julie Roy"
    assert [c["kind"] for c in data["consents"]] == ["terms", "marketing"] and data["appointments"] == []
    assert "password" not in json.dumps(data).lower()


def test_change_password_then_login_with_new_one(page):
    sign_up(page)
    open_profile(page)
    page.fill("input[name=current_password]", PASSWORD)
    page.fill("input[name=new_password]", "mon nouveau mot de passe")
    page.click("text=Changer le mot de passe")
    expect(page.locator("[role=status]")).to_contain_text("Mot de passe modifié")
    page.click("text=Déconnexion")
    page.goto("/auth/login")
    page.fill("input[name=email]", EMAIL)
    page.fill("input[name=password]", PASSWORD)  # l'ancien
    page.click("button[type=submit]")
    expect(page.locator(".error")).to_have_text("Email ou mot de passe incorrect.")
    expect(page.locator("input[name=email]")).to_have_value(EMAIL)  # l'email est conservé après une erreur
    page.fill("input[name=password]", "mon nouveau mot de passe")
    page.click("button[type=submit]")
    expect(page.locator("header").first).to_contain_text("Julie Roy")


def test_delete_account_flow(page, shot):
    sign_up(page)
    open_profile(page)
    zone = page.locator("#forget")
    expect(zone).to_contain_text("Effacé")
    expect(zone).to_contain_text("Conservé, sans vous identifier")
    shot("51-profil-suppression")

    # sans le mot de confirmation : refusé, rien n'est supprimé
    zone.locator("input[name=password]").fill(PASSWORD)
    zone.locator("input[name=confirmation]").fill("peut-être")
    zone.locator("button[type=submit]").click()
    expect(page.locator(".error")).to_contain_text("écrivez SUPPRIMER")
    with SessionLocal() as db:
        assert db.scalar(select(User).where(User.email == EMAIL)).anonymized_at is None

    # avec le bon mot de passe et le mot de confirmation : compte anonymisé, déconnexion, page de confirmation
    page.locator("#forget input[name=password]").fill(PASSWORD)
    page.locator("#forget input[name=confirmation]").fill("SUPPRIMER")
    page.locator("#forget button[type=submit]").click()
    expect(page).to_have_url(re.compile(r"/clients/deleted$"))
    expect(page.locator("h1")).to_have_text("Votre compte est supprimé")
    expect(page.locator("header").first).to_contain_text("Connexion")  # déconnectée
    shot("52-compte-supprime")
    with SessionLocal() as db:
        u = db.scalars(select(User).where(User.anonymized_at.is_not(None))).one()
        assert u.full_name == "CLIENT_SUPPRIMÉ" and u.email.endswith("@supprime.kyja") and u.phone is None
        assert db.scalar(select(User).where(User.email == EMAIL)) is None  # l'adresse est libérée

    page.goto("/clients/me")
    expect(page).to_have_url(re.compile(r"/auth/login\?next="))  # plus de session
    page.fill("input[name=email]", EMAIL)
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    expect(page.locator(".error")).to_have_text("Email ou mot de passe incorrect.")


def test_profile_has_no_horizontal_overflow_on_mobile(browser, base_url):
    ctx = browser.new_context(base_url=base_url, viewport={"width": 360, "height": 740})
    pg = ctx.new_page()
    pg.goto("/auth/register")
    pg.fill("input[name=full_name]", "Julie Roy")
    pg.fill("input[name=email]", EMAIL)
    pg.fill("input[name=password]", PASSWORD)
    pg.check("input[name=accept_terms]")
    pg.click("button[type=submit]")
    pg.goto("/clients/me")
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    pg.screenshot(path=str(SHOTS / "53-profil-mobile.png"), full_page=True)
    ctx.close()
