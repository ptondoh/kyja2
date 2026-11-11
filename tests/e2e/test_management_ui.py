"""Catégories, suppressions, stock et avis de prix dans un vrai navigateur."""
import json
import re
import zipfile

from playwright.sync_api import expect
from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models.core import Appointment, Category, Hairdresser, Product, Service
from tests.conftest import make_category, make_media_file, make_service, next_weekday
from tests.e2e.conftest import choose_hairdresser, pick_day
from tests.e2e.test_booking_flow import ADMIN, CLIENT, salon, sign_in  # noqa: F401  (fixture `salon`)


def admin_login(page, next_url="/admin"):
    page.goto(f"/auth/login?next={next_url}")
    sign_in(page, ADMIN)


# ---------------------------------------------------------------------------------------------- catégories


def test_create_category_then_pick_it_in_the_dropdown(page, shot, salon):
    admin_login(page, "/admin/categories")
    page.locator("form[method=post][action='/admin/categories'] input[name=name]").fill("TEST Coloration")
    page.locator("form[method=post][action='/admin/categories'] button[type=submit]").click()
    expect(page.locator("[role=status]")).to_have_text("Catégorie créée.")

    page.goto("/admin/services/new")
    options = page.locator("select[name=category_id] option").all_inner_texts()
    assert "— Sans catégorie —" in options and "TEST Coloration" in options  # liste déroulante
    page.fill("input[name=name]", "TEST Balayage")
    page.select_option("select[name=category_id]", label="TEST Coloration")
    page.fill("input[name=base_duration_min]", "120")
    page.fill("input[name=price]", "150")
    page.locator("form.card >> button[type=submit]").click()
    expect(page.locator(".ok").first).to_have_text("Enregistré.")
    page.goto("/services")
    expect(page.locator(".chips a", has_text="TEST Coloration")).to_be_visible()
    shot("60-categories-filtre")


def test_delete_category_shows_warning_offers_backup_and_deletes(page, shot, salon):
    with SessionLocal() as db:
        cat = make_category(db, "TEST Soins")
        svc = make_service(db, "TEST Masque", category=cat, price="30.00")
        make_media_file(db, svc, "image", "masque")
        cid, sid = cat.id, svc.id
    admin_login(page, f"/admin/categories/{cid}/delete")

    # l'avertissement est clair et la sauvegarde est proposée AVANT la suppression
    expect(page.locator("[role=alert]").first).to_contain_text("tout le contenu de cette catégorie disparaîtra")
    expect(page.locator("main")).to_contain_text("toutes ses prestations")
    expect(page.locator("main")).to_contain_text("TEST Masque")
    shot("61-categorie-suppression")
    with page.expect_download() as info:
        page.click("text=Télécharger la sauvegarde")
    download = info.value
    assert re.fullmatch(r"kyja-sauvegarde-test-soins-\d{8}\.zip", download.suggested_filename)
    z = zipfile.ZipFile(download.path())
    data = json.loads(z.read("categorie.json"))
    assert [s["name"] for s in data["services"]] == ["TEST Masque"]
    assert any(n.startswith(f"medias/{sid}/") for n in z.namelist())  # la photo est dans la sauvegarde

    # mauvais nom : refusé ; bon nom : supprimée
    page.fill("input[name=confirmation]", "autre chose")
    page.click("text=Supprimer définitivement la catégorie")
    expect(page.locator(".error")).to_contain_text("écrivez exactement le nom")
    page.fill("input[name=confirmation]", "TEST Soins")
    page.click("text=Supprimer définitivement la catégorie")
    expect(page).to_have_url(re.compile(r"/admin/categories\?removed=1"))
    expect(page.locator("[role=status]")).to_contain_text("1 prestation(s) effacée(s)")
    with SessionLocal() as db:
        assert db.get(Category, cid) is None and db.get(Service, sid) is None


# ---------------------------------------------------------------------------------------------- suppressions


def test_delete_service_and_product_from_the_admin_screens(page, salon):
    with SessionLocal() as db:
        svc = make_service(db, "TEST À retirer")
        p = Product(name="TEST Produit à retirer", price=12, stock=9)
        db.add(p)
        db.commit()
        sid, pid = svc.id, p.id
    admin_login(page, f"/admin/services/{sid}")
    page.click("text=Supprimer cette prestation")
    expect(page.locator("h1")).to_contain_text("Supprimer « TEST À retirer »")
    expect(page.locator("main")).to_contain_text("jamais été réservée")
    page.click("button:has-text('Supprimer définitivement')")
    expect(page).to_have_url(re.compile(r"/admin/services\?removed="))
    expect(page.locator("tr", has_text="TEST À retirer")).to_have_count(0)

    page.goto(f"/admin/products/{pid}")
    page.click("text=Supprimer ce produit")
    page.click("button:has-text('Supprimer définitivement')")
    expect(page.locator("[role=status]")).to_contain_text("Produit supprimé")
    assert page.goto(f"/products/{pid}").status == 404  # le produit n'existe plus


def test_delete_hairdresser_then_restore(page, shot, salon):
    admin_login(page, "/admin/hairdressers")
    row = page.locator("tr", has_text="Sonia")
    row.locator("text=Supprimer…").click()
    expect(page.locator("main")).to_contain_text("disparaît de la réservation")
    expect(page.locator("main")).to_contain_text("Conservé")
    shot("62-coiffeuse-suppression")
    page.click("button:has-text('Supprimer la coiffeuse')")
    expect(page.locator("[role=status]")).to_contain_text("Sonia")
    expect(page.locator("tr", has_text="Sonia")).to_have_count(0)  # plus dans la liste active

    page.click("text=Archivées")
    archived = page.locator("tr", has_text="Sonia")
    expect(archived).to_contain_text("sonia@test.kyja")  # coordonnées conservées
    archived.locator("text=Restaurer").click()
    expect(page.locator("main")).to_contain_text("Enregistré.")
    page.goto("/admin/hairdressers")
    expect(page.locator("tr", has_text="Sonia")).to_be_visible()


# ---------------------------------------------------------------------------------------------- stock


def test_low_stock_is_shown_in_red_next_to_in_stock(page, shot):
    with SessionLocal() as db:
        low, ok = Product(name="TEST Presque épuisé", price=10, stock=3), Product(name="TEST Beaucoup", price=10, stock=8)
        db.add_all([low, ok])
        db.commit()
        low_id, ok_id = low.id, ok.id
    page.goto(f"/products/{low_id}")
    badge = page.locator("[data-low-stock]")
    expect(page.locator("aside")).to_contain_text("En stock (3)")
    assert page.evaluate("(el) => getComputedStyle(el).color", badge.element_handle()) == "rgb(155, 28, 28)"  # rouge
    shot("63-stock-faible")
    page.goto(f"/products/{ok_id}")
    expect(page.locator("aside")).to_contain_text("En stock")
    expect(page.locator("[data-low-stock]")).to_have_count(0)
    expect(page.locator("aside")).not_to_contain_text("En stock (")


# ---------------------------------------------------------------------------------------------- avis de prix


def to_recap(page, day):
    page.locator(".tile", has_text="TEST Brushing").click()
    choose_hairdresser(page, page.locator(".choice", has_text="Marie"))
    pick_day(page, day)
    page.locator("#slots .slot", has_text=re.compile(r"^10 h$")).click()
    page.click("text=Voir le récapitulatif")


def test_price_dialog_before_confirming_a_catalog_booking(page, shot, salon):
    day = next_weekday(0).isoformat()
    page.goto("/auth/login?next=/bookings/new?mode=catalog")
    sign_in(page, CLIENT)
    to_recap(page, day)
    expect(page.locator(".price-notice")).to_contain_text("Prix estimatif")  # avis déjà visible dans le récapitulatif
    dialog = page.locator("#price-dialog")
    expect(dialog).not_to_be_visible()

    page.click("text=Confirmer la réservation")  # → fenêtre, aucune réservation encore
    expect(dialog).to_be_visible()
    expect(dialog).to_contain_text("Le prix est estimatif")
    expect(dialog).to_contain_text("analyse des photos et des vidéos")
    expect(dialog).to_contain_text("55.00 $ CAD")
    shot("64-popup-prix-estimatif")
    with SessionLocal() as db:
        assert db.scalars(select(Appointment)).all() == []

    dialog.locator("text=Retour").click()  # retour : la fenêtre se ferme, toujours rien de réservé
    expect(dialog).not_to_be_visible()
    expect(page.locator("h1")).to_have_text("Récapitulatif")
    page.click("text=Confirmer la réservation")
    page.keyboard.press("Escape")  # Échap ferme aussi la fenêtre
    expect(dialog).not_to_be_visible()
    with SessionLocal() as db:
        assert db.scalars(select(Appointment)).all() == []

    page.click("text=Confirmer la réservation")
    dialog.locator("text=J'ai compris, confirmer").click()
    expect(page.locator("[role=status]")).to_contain_text("Rendez-vous confirmé")
    expect(page.locator(".price-notice")).to_contain_text("Prix estimatif")  # rappelé sur le rendez-vous confirmé
    with SessionLocal() as db:
        assert len(db.scalars(select(Appointment)).all()) == 1  # une seule réservation, malgré les allers-retours
    assert page.console_errors == [], page.console_errors


def test_modifier_skips_the_dialog(page, salon):
    day = next_weekday(0).isoformat()
    page.goto("/auth/login?next=/bookings/new?mode=catalog")
    sign_in(page, CLIENT)
    to_recap(page, day)
    page.click("text=Modifier")  # revenir en arrière ne demande aucune confirmation
    expect(page.locator("#price-dialog")).to_have_count(0)
    expect(page.locator(".cal")).to_be_visible()


def test_visitor_gets_the_dialog_after_signing_up_not_before(page, salon):
    day = next_weekday(0).isoformat()
    page.goto("/bookings/new?mode=catalog")
    to_recap(page, day)
    expect(page.locator(".price-notice")).to_be_visible()
    expect(page.locator("#price-dialog")).to_have_count(0)  # pas de confirmation possible, donc pas de fenêtre
    page.click("#login-wall >> text=Créer mon compte")
    page.fill("input[name=full_name]", "Nouvelle Cliente")
    page.fill("input[name=email]", "nouvelle-fenetre@test.kyja")
    page.fill("input[name=password]", "un mot de passe solide")
    page.check("input[name=accept_terms]")
    page.click("button[type=submit]")
    expect(page).to_have_url(re.compile(r"/bookings/resume$"))
    page.click("text=Confirmer la réservation")
    expect(page.locator("#price-dialog")).to_be_visible()  # la fenêtre apparaît au moment de confirmer
    page.locator("#price-dialog >> text=J'ai compris, confirmer").click()
    expect(page.locator("[role=status]")).to_contain_text("Rendez-vous confirmé")
