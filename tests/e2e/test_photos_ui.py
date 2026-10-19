"""Demande sur mesure : zone de texte + jusqu'à 10 photos avec aperçu immédiat (navigateur réel)."""
import re

import pytest
from PIL import Image
from playwright.sync_api import expect
from sqlalchemy import func, select

from app.db.base import SessionLocal
from app.db.models.core import BookingPhoto
from tests.conftest import next_weekday
from tests.e2e.conftest import SHOTS, pick_day
from tests.e2e.test_booking_flow import ADMIN, CLIENT, salon, sign_in  # noqa: F401  (fixture `salon`)

NOTES = "Tresses africaines avec rajouts, longueur milieu du dos, couleur noir et bordeaux"


@pytest.fixture
def images(tmp_path):
    paths = []
    for i in range(12):
        p = tmp_path / f"inspiration-{i:02d}.png"
        Image.new("RGB", (640 + i * 10, 480), (20 * i % 255, 120, 200 - 10 * i)).save(p)
        paths.append(str(p))
    txt = tmp_path / "notes.txt"
    txt.write_text("pas une image")
    return paths, str(txt)


def drafts_count() -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(BookingPhoto).where(BookingPhoto.appointment_id.is_(None)))


def photos_count() -> int:
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(BookingPhoto))


def wait_images_loaded(page, selector, expected=None, timeout_ms=10000):
    """Attend que toutes les images soient affichées. (`wait_for_function` utilise eval : bloqué par la CSP.)"""
    waited = 0
    while waited <= timeout_ms:
        state = page.evaluate(
            "(sel) => { const i = [...document.querySelectorAll(sel)]; return [i.length, i.every(x => x.complete && x.naturalWidth > 0)]; }",
            selector,
        )
        if state[1] and state[0] > 0 and (expected is None or state[0] == expected):
            return
        page.wait_for_timeout(100)
        waited += 100
    raise AssertionError(f"images non chargées : {selector} {state}")


def wait_drafts(expected, timeout_ms=5000):
    import time

    end = time.time() + timeout_ms / 1000
    while time.time() < end:
        if drafts_count() == expected:
            return
        time.sleep(0.1)
    assert drafts_count() == expected


def open_custom_form(page):
    page.goto("/auth/login?next=/bookings/new?mode=custom")
    sign_in(page, CLIENT)
    page.locator(".tile", has_text="TEST Tresses").click()
    expect(page.locator("textarea[name=notes]")).to_be_visible()


def test_previews_appear_immediately_then_upload(page, shot, salon, images):
    paths, _ = images
    open_custom_form(page)
    expect(page.locator("#photo-count")).to_have_text("0 / 10 photos")

    page.set_input_files("#photo-input", paths[:3])
    # aperçu immédiat : les miniatures sont là (images locales « blob: » lisibles) avant la fin de l'envoi
    expect(page.locator("#photo-previews img")).to_have_count(3)
    wait_images_loaded(page, "#photo-previews img", expected=3)
    expect(page.locator("#photo-count")).to_have_text("3 / 10 photos")

    # puis chaque photo est envoyée et rattachée au formulaire
    expect(page.locator("#photo-previews .is-uploaded")).to_have_count(3)
    expect(page.locator("#photo-previews input[name=photo_ids]")).to_have_count(3, timeout=10000)
    assert drafts_count() == 3
    shot("30-photos-apercu")

    # retrait d'une photo : disparaît de la page ET du serveur
    page.locator("#photo-previews .photo-remove").first.click()
    expect(page.locator("#photo-previews [data-photo]")).to_have_count(2)
    expect(page.locator("#photo-count")).to_have_text("2 / 10 photos")
    wait_drafts(2)
    assert page.console_errors == [], page.console_errors  # aucune violation CSP (aperçus blob inclus)


def test_limit_of_ten_and_format_refused(page, shot, salon, images):
    paths, txt = images
    open_custom_form(page)
    page.set_input_files("#photo-input", paths[:8])
    expect(page.locator("#photo-previews .is-uploaded")).to_have_count(8, timeout=30000)

    page.set_input_files("#photo-input", paths[8:12])  # 4 de plus : seules 2 passent
    expect(page.locator("#photo-message")).to_contain_text("maximum 10 photos atteint")
    expect(page.locator("#photo-previews [data-photo]")).to_have_count(10)
    expect(page.locator("#photo-previews .is-uploaded")).to_have_count(10, timeout=30000)
    expect(page.locator("#photo-count")).to_have_text("10 / 10 photos")
    expect(page.locator("#photo-input")).to_be_disabled()  # plus d'ajout possible
    assert drafts_count() == 10
    shot("31-photos-maximum")

    page.locator("#photo-previews .photo-remove").first.click()  # on libère une place
    expect(page.locator("#photo-input")).to_be_enabled()
    expect(page.locator("#photo-count")).to_have_text("9 / 10 photos")

    page.set_input_files("#photo-input", txt)  # format non pris en charge : refusé avant tout envoi
    expect(page.locator("#photo-message")).to_contain_text("format non pris en charge")
    expect(page.locator("#photo-previews [data-photo]")).to_have_count(9)
    assert drafts_count() == 9
    assert page.console_errors == [], page.console_errors


def test_full_custom_flow_with_photos_edit_and_admin_view(page, browser, base_url, shot, salon, images):
    paths, _ = images
    open_custom_form(page)
    page.set_input_files("#photo-input", paths[:3])
    expect(page.locator("#photo-previews .is-uploaded")).to_have_count(3, timeout=15000)
    page.fill("textarea[name=notes]", NOTES)
    page.locator(".choice", has_text="Peu importe").click()
    pick_day(page, next_weekday(0).isoformat())
    expect(page.locator("#slots .slot").first).to_be_visible()
    page.locator("#slots .slot").first.click()
    shot("32-sur-mesure-complet")
    page.click("text=Voir le récapitulatif")

    # récapitulatif : description + miniatures lisibles (servies par la route privée)
    expect(page.locator(".recap")).to_contain_text("Photos d'inspiration (3)")
    expect(page.locator(".recap")).to_contain_text("Tresses africaines")
    wait_images_loaded(page, ".recap .photo-grid img", expected=3)
    shot("33-recap-photos")

    # « Modifier » : retour au formulaire sans perdre description ni photos
    page.click("text=Modifier")
    expect(page.locator("textarea[name=notes]")).to_have_value(NOTES)
    expect(page.locator("#photo-previews [data-photo]")).to_have_count(3)
    expect(page.locator("#photo-count")).to_have_text("3 / 10 photos")
    wait_images_loaded(page, "#photo-previews img", expected=3)

    page.locator(".choice", has_text="Peu importe").click()
    pick_day(page, next_weekday(0).isoformat())
    expect(page.locator("#slots .slot").first).to_be_visible()
    page.locator("#slots .slot").first.click()
    page.click("text=Voir le récapitulatif")
    page.click("text=Confirmer la réservation")
    expect(page.locator("[role=status]")).to_contain_text("Rendez-vous confirmé")
    wait_images_loaded(page, ".recap .photo-grid img", expected=3)
    shot("34-detail-photos")
    assert drafts_count() == 0 and photos_count() == 3  # rattachées au rendez-vous, plus de brouillons

    # le salon (admin) voit la demande et les photos
    ctx = browser.new_context(base_url=base_url)
    admin = ctx.new_page()
    admin.goto("/auth/login?next=/admin/appointments")
    sign_in(admin, ADMIN)
    row = admin.locator("tr", has_text="TEST Tresses")
    expect(row).to_contain_text("Tresses africaines")
    expect(row.locator(".photo-card img")).to_have_count(3)
    wait_images_loaded(admin, ".photo-card img", expected=3)
    admin.screenshot(path=str(SHOTS / "35-admin-photos.png"), full_page=True)
    ctx.close()
    assert page.console_errors == [], page.console_errors


def test_photo_grid_has_no_horizontal_overflow_on_mobile(browser, base_url, salon, images):
    paths, _ = images
    ctx = browser.new_context(base_url=base_url, viewport={"width": 360, "height": 740})
    pg = ctx.new_page()
    pg.goto("/auth/login?next=/bookings/new?mode=custom")
    sign_in(pg, CLIENT)
    pg.locator(".tile", has_text="TEST Tresses").click()
    pg.set_input_files("#photo-input", paths[:10])
    expect(pg.locator("#photo-previews .is-uploaded")).to_have_count(10, timeout=30000)
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    pg.screenshot(path=str(SHOTS / "36-photos-mobile.png"), full_page=True)
    ctx.close()


def test_catalog_mode_has_no_photo_zone(page, salon):
    page.goto("/auth/login?next=/bookings/new?mode=catalog")
    sign_in(page, CLIENT)
    page.locator(".tile", has_text="TEST Brushing").click()
    expect(page).to_have_url(re.compile(r"/bookings/new/\d+$"))
    expect(page.locator("#photo-input")).to_have_count(0)
    expect(page.locator("textarea[name=notes]")).not_to_have_attribute("required", "")
