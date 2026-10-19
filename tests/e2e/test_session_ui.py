"""Renouvellement silencieux de la session dans un vrai navigateur (le cookie d'accès expire en 15 min)."""
import re

from PIL import Image
from playwright.sync_api import expect

from tests.conftest import next_weekday
from tests.e2e.conftest import choose_hairdresser, pick_day
from tests.e2e.test_booking_flow import ADMIN, CLIENT, salon, sign_in  # noqa: F401  (fixture `salon`)


def expire_session(page):
    """Ce que fait le navigateur après 15 minutes : le cookie d'accès disparaît (le renouvellement reste)."""
    page.context.clear_cookies(name="access_token")
    names = {c["name"] for c in page.context.cookies()}
    assert "access_token" not in names and "refresh_token" in names


def cookies_of(page):
    return {c["name"]: c for c in page.context.cookies()}


def test_navigation_after_expiry_stays_logged_in(page, salon):
    page.goto("/auth/login")
    sign_in(page, CLIENT)
    expect(page.locator("header").first).to_contain_text("Cliente E2E")
    first_refresh = cookies_of(page)["refresh_token"]["value"]

    expire_session(page)
    page.goto("/bookings")  # sans le correctif : page de connexion
    expect(page.locator("h1")).to_have_text("Mes rendez-vous")
    expect(page.locator("header").first).to_contain_text("Cliente E2E")
    cookies = cookies_of(page)
    assert "access_token" in cookies  # un nouveau cookie d'accès est revenu
    assert cookies["refresh_token"]["value"] != first_refresh  # le renouvellement a tourné (rotation)
    assert cookies["refresh_token"]["httpOnly"] and cookies["refresh_token"]["sameSite"] == "Strict"
    assert page.evaluate("document.cookie") == ""  # aucun des deux illisible par JavaScript


def test_admin_form_is_saved_even_if_the_session_expired_meanwhile(page, salon):
    page.goto("/auth/login?next=/admin/settings")
    sign_in(page, ADMIN)
    page.fill("input[name=booking_horizon_days]", "45")
    expire_session(page)  # l'admin a pris son temps : plus de 15 minutes se sont écoulées
    page.locator("form.card >> button[type=submit]").click()
    expect(page.locator(".ok")).to_have_text("Enregistré.")  # pas de formulaire perdu, pas de reconnexion
    expect(page.locator("input[name=booking_horizon_days]")).to_have_value("45")


def test_confirming_a_booking_after_expiry_needs_no_new_login(page, shot, salon):
    day = next_weekday(0).isoformat()
    page.goto("/auth/login?next=/bookings/new?mode=catalog")
    sign_in(page, CLIENT)
    page.locator(".tile", has_text="TEST Brushing").click()
    choose_hairdresser(page, page.locator(".choice", has_text="Marie"))
    pick_day(page, day)
    page.locator("#slots .slot", has_text=re.compile(r"^10 h$")).click()
    page.click("text=Voir le récapitulatif")
    expect(page.locator("text=Confirmer la réservation")).to_be_visible()

    expire_session(page)  # la session expire pendant que la cliente relit son récapitulatif
    page.click("text=Confirmer la réservation")
    expect(page.locator("[role=status]")).to_contain_text("Rendez-vous confirmé")  # direct, sans passer par la connexion
    assert page.console_errors == [], page.console_errors


def test_parallel_protected_images_do_not_log_the_user_out(page, salon, tmp_path):
    """Une page avec plusieurs images protégées : le navigateur les demande en parallèle, toutes avec
    l'ancien jeton de renouvellement. Aucune ne doit être prise pour un vol (déconnexion générale)."""
    imgs = []
    for i in range(6):
        p = tmp_path / f"p{i}.png"
        Image.new("RGB", (400 + i * 20, 300), (30 * i, 100, 200)).save(p)
        imgs.append(str(p))
    page.goto("/auth/login?next=/bookings/new?mode=custom")
    sign_in(page, CLIENT)
    page.locator(".tile", has_text="Autre demande").click()  # la prestation intégrée (le fixture crée aussi un type « sur devis »)
    page.set_input_files("#photo-input", imgs)
    expect(page.locator("#photo-previews .is-uploaded")).to_have_count(6, timeout=30000)

    expire_session(page)
    page.reload()  # 6 images protégées demandées en parallèle, session expirée
    expect(page.locator("header").first).to_contain_text("Cliente E2E")  # toujours connectée
    expect(page.locator("#photo-previews [data-photo]")).to_have_count(6)
    page.wait_for_load_state("networkidle")
    loaded = page.evaluate("[...document.querySelectorAll('#photo-previews img')].filter(i => i.complete && i.naturalWidth > 0).length")
    assert loaded == 6  # toutes les images protégées se sont chargées
    # et la session n'a pas été coupée par une fausse alerte de vol : on peut continuer à naviguer
    page.goto("/bookings")
    expect(page.locator("h1")).to_have_text("Mes rendez-vous")
    assert page.console_errors == [], page.console_errors


def test_explicit_logout_still_logs_out_after_expiry(page, salon):
    page.goto("/auth/login")
    sign_in(page, CLIENT)
    expire_session(page)
    page.goto("/")
    expect(page.locator("header").first).to_contain_text("Cliente E2E")  # la page d'accueil a renouvelé la session
    page.click("text=Déconnexion")
    expect(page.locator("header").first).to_contain_text("Connexion")
    page.goto("/bookings")
    expect(page).to_have_url(re.compile(r"/auth/login\?next="))  # bien déconnectée : le renouvellement n'a pas défait la déconnexion
    assert "refresh_token" not in cookies_of(page)
