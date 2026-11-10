"""Menu actif, cloche rouge, carrousel avec flèches, fenêtre centrée, déplacement d'un rendez-vous."""
import re
from datetime import timedelta

from playwright.sync_api import expect
from sqlalchemy import select

from app.bookings import engine
from app.db.base import SessionLocal
from app.db.models.core import Appointment, Hairdresser, Notification, User
from tests.conftest import local_start, make_media_file, make_service, next_weekday
from tests.e2e.conftest import choose_hairdresser, pick_day
from tests.e2e.test_booking_flow import CLIENT, salon, sign_in  # noqa: F401  (fixture `salon`)
from tests.e2e.test_quotes_ui import HAIRDRESSER


def test_only_the_current_page_is_highlighted_in_the_menu(page, salon):
    page.goto("/auth/login?next=/agenda")
    sign_in(page, HAIRDRESSER)
    expect(page.locator("nav[aria-label='Navigation principale'] a[aria-current=page]")).to_have_text("Mon agenda")
    page.goto("/services")
    current = page.locator("nav[aria-label='Navigation principale'] a[aria-current=page]")
    expect(current).to_have_count(1)
    expect(current).to_have_text("Prestations")
    page.goto("/bookings")
    expect(current).to_have_text("Mes rendez-vous")
    # « Mon agenda » n'a plus la couleur « active » quand on est ailleurs
    page.goto("/products")
    agenda = page.locator("nav a", has_text="Mon agenda")
    assert agenda.evaluate("e => e.classList.contains('is-active')") is False


def test_bell_shows_a_visible_red_counter(page, shot, salon):
    with SessionLocal() as db:
        client = db.scalar(select(User).where(User.email == CLIENT[0]))
        for i in range(3):
            db.add(Notification(user_id=client.id, kind="test", title=f"Alerte {i}", body="x"))
        db.commit()
    page.goto("/auth/login?next=/services")
    sign_in(page, CLIENT)
    badge = page.locator("[data-bell-count]")
    expect(badge).to_have_text("3", timeout=10000)
    expect(page.locator("#bell svg")).to_be_visible()
    bg = badge.evaluate("e => getComputedStyle(e).backgroundColor")
    fg = badge.evaluate("e => getComputedStyle(e).color")
    assert bg == "rgb(229, 50, 45)" and fg == "rgb(255, 255, 255)", (bg, fg)
    shot("73-cloche")


def test_carousel_arrows_are_visible_and_everything_is_centered(page, shot, salon):
    with SessionLocal() as db:
        svc = make_service(db, "TEST Galerie centrée", price="80.00")
        for n in "abc":
            make_media_file(db, svc, "image", n)
        make_media_file(db, svc, "video", "v")
        sid = svc.id
    page.goto(f"/services/{sid}")
    assert page.locator(".gallery video").first.evaluate("e => getComputedStyle(e).objectFit") == "contain"  # format respecté, jamais rogné
    page.locator(".gallery-photo").first.click()
    box = page.locator("dialog.lightbox[open]")
    expect(box.locator(".lightbox-next")).to_be_visible()
    expect(box.locator(".lightbox-prev")).to_be_visible()
    vp = page.viewport_size
    r = box.bounding_box()
    assert abs(r["x"]) < 1 and abs(r["y"]) < 1 and abs(r["width"] - vp["width"]) < 2 and abs(r["height"] - vp["height"]) < 2
    nxt = box.locator(".lightbox-next").bounding_box()
    assert abs((nxt["y"] + nxt["height"] / 2) - vp["height"] / 2) < 3  # flèche à mi-hauteur
    expect(box.locator(".lightbox-count")).to_contain_text("1 / 4")


def test_price_dialog_is_centered_in_the_window(page, salon):
    page.goto("/bookings/new")
    page.click("text=Choisir une coiffure")
    page.locator(".tile", has_text="TEST Brushing").click()
    choose_hairdresser(page, page.locator(".choice", has_text="Marie"))
    pick_day(page, next_weekday(0).isoformat())
    page.locator("#slots .slot", has_text=re.compile(r"^10 h$")).click()
    page.click("text=Voir le récapitulatif")
    page.click("#login-wall >> text=J'ai déjà un compte")
    sign_in(page, CLIENT)
    page.click("text=Confirmer la réservation")
    dialog = page.locator("#price-dialog")
    dialog.wait_for(state="visible")
    vp, r = page.viewport_size, dialog.bounding_box()
    assert abs((r["x"] + r["width"] / 2) - vp["width"] / 2) < 3
    assert abs((r["y"] + r["height"] / 2) - vp["height"] / 2) < 3


def test_client_moves_her_confirmed_appointment_from_the_site(page, shot, salon):
    with SessionLocal() as db:
        client = db.scalar(select(User).where(User.email == CLIENT[0]))
        marie = db.scalar(select(Hairdresser).join(User).where(User.full_name == "Marie"))
        svc = make_service(db, "TEST Déplacement", base=60, price="60.00")
        appt = engine.create_booking(db, client=client, service=svc, hairdresser_id=marie.id, start=local_start(next_weekday(0), 10))
        appt_id = appt.id
    page.goto(f"/auth/login?next=/bookings/{appt_id}")
    sign_in(page, CLIENT)
    page.click("text=Changer la date ou l'heure")
    expect(page.locator("h1")).to_have_text("Changer la date ou l'heure")
    pick_day(page, (next_weekday(0) + timedelta(days=7)).isoformat())
    page.locator("#slots .slot", has_text=re.compile(r"^14 h$")).click()
    shot("74-deplacer")
    page.click("text=Confirmer le nouvel horaire")
    expect(page.locator("[role=status]")).to_contain_text("Rendez-vous déplacé")
    with SessionLocal() as db:
        moved = db.get(Appointment, appt_id)
        assert moved.starts_at == local_start(next_weekday(0) + timedelta(days=7), 14)


def test_appointment_filters_are_typing_zones_in_the_column_headers(browser, base_url, page, shot, salon):
    with SessionLocal() as db:
        client = db.scalar(select(User).where(User.email == CLIENT[0]))
        marie = db.scalar(select(Hairdresser).join(User).where(User.full_name == "Marie"))
        svc = make_service(db, "TEST Filtre", base=60, price="60.00")
        engine.create_booking(db, client=client, service=svc, hairdresser_id=marie.id, start=local_start(next_weekday(0), 10))
        engine.create_booking(db, client=client, service=engine.custom_request_service(db), hairdresser_id=marie.id,
                              start=local_start(next_weekday(0), 14), notes="Tresses africaines avec rajouts")  # fmt: skip
    page.goto("/auth/login?next=/admin/appointments")
    sign_in(page, ("booking-admin@test.kyja", "admin password 123"))
    rows = page.locator("table.admin-table tbody tr")
    expect(rows).to_have_count(2)
    # un filtre = une zone de saisie dans l'en-tête de sa colonne, avec sa liste (flèche du navigateur)
    expect(page.locator("tr.filter-row input[name=statut]")).to_have_attribute("list", "dl-statut")
    assert page.locator("#dl-statut option").count() == 5
    shot("75-filtres")
    page.fill("tr.filter-row input[name=statut]", "prix a prop")  # quelques lettres, sans accent
    page.press("tr.filter-row input[name=statut]", "Enter")
    expect(page).to_have_url(re.compile(r"statut=prix"))
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("sur mesure")
    page.fill("tr.filter-row input[name=statut]", "")
    page.press("tr.filter-row input[name=statut]", "Enter")  # zone vidée : le filtre disparaît
    expect(rows).to_have_count(2)
    page.fill("tr.filter-row input[name=type]", "catalog")
    page.press("tr.filter-row input[name=type]", "Enter")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("TEST Filtre")
    page.fill("tr.filter-row input[name=type]", "")
    page.press("tr.filter-row input[name=type]", "Enter")
    expect(rows).to_have_count(2)
    page.fill("tr.filter-row input[name=coiffeuse]", "sonia")
    page.press("tr.filter-row input[name=coiffeuse]", "Enter")
    expect(page.locator("table.admin-table tbody")).to_contain_text("Aucun rendez-vous")


def test_every_list_uses_the_same_column_filters(page, shot, salon):
    from app.db.models.core import Product

    with SessionLocal() as db:
        db.add(Product(name="TEST Shampoing", price=20, stock=2))
        db.add(Product(name="TEST Huile", price=30, stock=40))
        db.commit()
    page.goto("/auth/login?next=/admin/products")
    sign_in(page, ("booking-admin@test.kyja", "admin password 123"))
    rows = page.locator("table.admin-table tbody tr")

    def filter_by(name, text):
        box = page.locator(f"tr.filter-row input[name={name}]")
        box.fill(text)
        box.press("Enter")

    filter_by("stock", "bas")  # liste « Stock » : stock bas
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("TEST Shampoing")
    page.goto("/admin/products")
    filter_by("q", "huil")
    expect(rows).to_have_count(1)
    expect(rows.first).to_contain_text("TEST Huile")
    for path, field, text, found in (
        ("/admin/services", "q", "brush", "TEST Brushing"),
        ("/admin/hairdressers", "q", "mari", "Marie"),
        ("/admin/messages", "canal", "sms", None),
    ):
        page.goto(path)
        expect(page.locator(f"tr.filter-row input[name={field}]")).to_be_visible()
        filter_by(field, text)
        expect(page).to_have_url(re.compile(f"{field}="))
        if found:
            expect(page.locator("table.admin-table tbody")).to_contain_text(found)
    shot("76-filtres-messages")
