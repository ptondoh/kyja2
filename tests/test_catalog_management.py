"""Catégories gérées par l'admin, suppression / archivage sans perdre l'historique, stock faible, avis de prix."""
import html
import io
import json
import re
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.bookings import engine
from app.config import get_settings
from app.db.base import SessionLocal
from app.db.models.core import (
    Appointment,
    AppointmentStatus,
    Category,
    Hairdresser,
    Media,
    Product,
    Service,
    User,
)
from app.main import app
from tests.conftest import (
    csrf,
    local_start,
    login,
    make_admin,
    make_category,
    make_client_user,
    make_hairdresser,
    make_media_file,
    make_service,
    next_weekday,
    register,
)


def visitor():
    c = TestClient(app, follow_redirects=False)
    c.get("/auth/login")
    return c


@pytest.fixture
def admin():
    c = visitor()
    make_admin(c)
    return c


def post(c, path, **data):
    return c.post(path, data={"csrf_token": csrf(c), **data})


def media_path(m: Media) -> Path:
    return Path(get_settings().media_dir) / m.storage_key


def book(db, service, hairdresser, day_offset=0, hour=10, client=None):
    client = client or make_client_user(db, f"c{day_offset}{hour}@test.kyja")
    return engine.create_booking(db, client=client, service=service, hairdresser_id=hairdresser.id,
                                 start=local_start(next_weekday(0) + timedelta(days=day_offset), hour))  # fmt: skip


def make_past(db, appt):
    appt.starts_at = datetime.now(timezone.utc) - timedelta(days=3)
    appt.ends_at = appt.starts_at + timedelta(hours=1)
    db.commit()


# ============================================================ catégories : création et liste déroulante


def test_category_pages_are_admin_only(admin):
    c = visitor()
    register(c)
    for path in ("/admin/categories", "/admin/categories/1/delete", "/admin/categories/1/backup"):
        assert c.get(path).status_code == 403
    assert post(c, "/admin/categories", name="TEST Pirate").status_code == 403
    assert visitor().get("/admin/categories").status_code == 401


def test_create_rename_and_uniqueness_of_categories(admin):
    assert post(admin, "/admin/categories", name="  TEST Coiffure  ").status_code == 303
    with SessionLocal() as db:
        cat = db.scalar(select(Category).where(Category.name == "TEST Coiffure"))  # espaces retirés
    assert "TEST Coiffure" in admin.get("/admin/categories").text
    # doublon refusé sans tenir compte des majuscules
    r = post(admin, "/admin/categories", name="test COIFFURE")
    assert r.status_code == 400 and "porte déjà ce nom" in html.unescape(r.text)
    assert post(admin, "/admin/categories", name="").status_code == 400
    assert post(admin, "/admin/categories", name="x" * 101).status_code == 400
    # renommer : un autre nom libre, ou changer la casse du sien, mais pas prendre le nom d'une autre
    assert post(admin, "/admin/categories", name="TEST Soins").status_code == 303
    assert post(admin, f"/admin/categories/{cat.id}", name="TEST COIFFURE").status_code == 303
    r = post(admin, f"/admin/categories/{cat.id}", name="test soins")
    assert r.status_code == 400 and "porte déjà ce nom" in html.unescape(r.text)
    assert admin.post("/admin/categories", data={"name": "TEST Sans csrf"}).status_code == 403


def test_service_form_offers_a_dropdown_of_categories(admin):
    with SessionLocal() as db:
        a, b = make_category(db, "TEST Coiffure"), make_category(db, "TEST Soins")
    page = admin.get("/admin/services/new").text
    assert '<select name="category_id">' in page and "— Sans catégorie —" in page
    assert f'<option value="{a.id}"' in page and f'<option value="{b.id}"' in page and "TEST Soins" in page
    assert 'name="category"' not in page  # plus de texte libre


def test_service_saves_with_a_category_and_rejects_unknown_ones(admin):
    with SessionLocal() as db:
        cat = make_category(db, "TEST Soins")
    base = {"name": "TEST Soin visage", "base_duration_min": "45", "price": "40", "pricing_mode": "estimate", "is_active": "true"}
    assert post(admin, "/admin/services", category_id=str(cat.id), **base).status_code == 303
    with SessionLocal() as db:
        assert db.scalar(select(Service).where(Service.name == "TEST Soin visage")).category.name == "TEST Soins"
    for bad in ("999999", "abc", "-1"):
        r = post(admin, "/admin/services", category_id=bad, **{**base, "name": "TEST Autre"})
        assert r.status_code == 400 and "Catégorie invalide" in html.unescape(r.text)
    assert post(admin, "/admin/services", category_id="", **{**base, "name": "TEST Sans"}).status_code == 303  # « sans catégorie »


def test_public_filters_use_categories_and_group_services(admin):
    with SessionLocal() as db:
        coif, soins = make_category(db, "TEST Coiffure"), make_category(db, "TEST Soins")
        make_service(db, "TEST Coupe", category=coif)
        make_service(db, "TEST Masque", category=soins)
        make_service(db, "TEST Divers")  # sans catégorie
        make_category(db, "TEST Vide")  # aucune prestation : pas de filtre
    v = visitor()
    page = v.get("/services").text
    assert "TEST Coiffure" in page and "TEST Soins" in page and "TEST Vide" not in page
    assert page.index("TEST Coupe") < page.index("TEST Masque") < page.index("TEST Divers")  # sans catégorie à la fin
    only = v.get("/services?category=test%20soins").text  # insensible à la casse
    assert "TEST Masque" in only and "TEST Coupe" not in only and "TEST Divers" not in only


# ============================================================ supprimer une prestation


def test_delete_never_booked_service_removes_it_and_its_files(admin):
    with SessionLocal() as db:
        svc = make_service(db, "TEST Jamais réservée")
        m = make_media_file(db, svc)
        sid = svc.id
    path = media_path(m)
    assert path.exists()
    page = admin.get(f"/admin/services/{sid}/delete").text
    assert "supprimée définitivement" in html.unescape(page) and "Supprimer définitivement" in page
    r = post(admin, f"/admin/services/{sid}/delete")
    assert r.status_code == 303 and "removed=TEST" in r.headers["location"]
    with SessionLocal() as db:
        assert db.get(Service, sid) is None and db.scalars(select(Media).where(Media.service_id == sid)).all() == []
    assert not path.exists()  # le fichier est effacé aussi
    assert visitor().get(f"/services/{sid}").status_code == 404


def test_delete_booked_service_archives_it_and_keeps_history(admin):
    with SessionLocal() as db:
        h = make_hairdresser(db, "Marie", slots=tuple((d, 540, 1020) for d in range(7)))
        svc = make_service(db, "TEST Déjà réservée", category=make_category(db, "TEST Coiffure"))
        appt = book(db, svc, h)
        make_past(db, appt)
        appt_id, sid = appt.id, svc.id
    assert "archivée" in html.unescape(admin.get(f"/admin/services/{sid}/delete").text) and "Archiver la prestation" in admin.get(f"/admin/services/{sid}/delete").text
    r = post(admin, f"/admin/services/{sid}/delete")
    assert r.status_code == 303 and "archived=TEST" in r.headers["location"]
    with SessionLocal() as db:
        s = db.get(Service, sid)
        assert s.archived_at is not None and s.is_active is False  # archivée, pas supprimée
        a = db.get(Appointment, appt_id)
        assert a.service_id == sid and a.status == AppointmentStatus.confirmed and a.service.name == "TEST Déjà réservée"
    # invisible partout côté public
    v = visitor()
    assert v.get(f"/services/{sid}").status_code == 404 and "TEST Déjà réservée" not in v.get("/services").text
    assert "TEST Déjà réservée" not in v.get("/").text and "TEST Déjà réservée" not in v.get("/bookings/new?mode=catalog").text
    assert v.get(f"/bookings/new/{sid}").status_code == 404  # on ne peut plus la réserver
    # l'admin la retrouve dans « Archivées », et l'historique des rendez-vous la nomme toujours
    assert "TEST Déjà réservée" not in admin.get("/admin/services").text
    assert "TEST Déjà réservée" in admin.get("/admin/services?archived=1").text
    assert "TEST Déjà réservée" in admin.get("/admin/appointments").text


def test_restore_archived_service(admin):
    with SessionLocal() as db:
        h = make_hairdresser(db, "Marie", slots=tuple((d, 540, 1020) for d in range(7)))
        svc = make_service(db, "TEST Archivée")
        make_past(db, book(db, svc, h))
        sid = svc.id
    post(admin, f"/admin/services/{sid}/delete")
    assert visitor().get(f"/services/{sid}").status_code == 404
    assert "archivée" in html.unescape(admin.get(f"/admin/services/{sid}").text)
    assert post(admin, f"/admin/services/{sid}/restore").status_code == 303
    assert visitor().get(f"/services/{sid}").status_code == 200
    # cocher « visible » dans le formulaire d'une prestation archivée la restaure aussi
    post(admin, f"/admin/services/{sid}/delete")
    post(admin, f"/admin/services/{sid}", name="TEST Archivée", base_duration_min="60", price="55", is_active="true")
    assert visitor().get(f"/services/{sid}").status_code == 200


def test_cannot_delete_service_with_upcoming_appointments(admin):
    with SessionLocal() as db:
        h = make_hairdresser(db, "Marie", slots=tuple((d, 540, 1020) for d in range(7)))
        svc = make_service(db, "TEST À venir")
        book(db, svc, h)
        sid = svc.id
    page = admin.get(f"/admin/services/{sid}/delete").text
    assert "rendez-vous à venir" in html.unescape(page) and "Supprimer définitivement" not in page and "Archiver la prestation" not in page
    r = post(admin, f"/admin/services/{sid}/delete")
    assert r.status_code == 400 and "annulez-les d'abord" in html.unescape(r.text)
    with SessionLocal() as db:
        assert db.get(Service, sid).archived_at is None


def test_builtin_custom_request_cannot_be_deleted(admin):
    with SessionLocal() as db:
        bid = engine.custom_request_service(db).id
    assert admin.get(f"/admin/services/{bid}/delete").status_code == 400
    assert post(admin, f"/admin/services/{bid}/delete").status_code == 400
    assert "ne peut pas être supprimée" in html.unescape(admin.get(f"/admin/services/{bid}").text)
    with SessionLocal() as db:
        assert db.get(Service, bid) is not None


def test_service_deletion_is_admin_only_and_csrf_protected(admin):
    with SessionLocal() as db:
        sid = make_service(db, "TEST Protégée").id
    c = visitor()
    register(c)
    assert post(c, f"/admin/services/{sid}/delete").status_code == 403
    assert admin.post(f"/admin/services/{sid}/delete").status_code == 403  # sans jeton CSRF
    with SessionLocal() as db:
        assert db.get(Service, sid) is not None


# ============================================================ supprimer un produit


def test_delete_product_removes_files_and_page(admin):
    with SessionLocal() as db:
        p = Product(name="TEST Shampoing", price=10, stock=4)
        db.add(p)
        db.commit()
        m = make_media_file(db, p)
        pid = p.id
    assert media_path(m).exists() and visitor().get(f"/products/{pid}").status_code == 200
    assert "supprimé définitivement" in html.unescape(admin.get(f"/admin/products/{pid}/delete").text)
    r = post(admin, f"/admin/products/{pid}/delete")
    assert r.status_code == 303 and "removed=TEST" in r.headers["location"]
    with SessionLocal() as db:
        assert db.get(Product, pid) is None
    assert not media_path(m).exists() and visitor().get(f"/products/{pid}").status_code == 404
    assert "TEST Shampoing" in admin.get(r.headers["location"]).text  # message de confirmation


# ============================================================ supprimer une catégorie (avertissement + sauvegarde)


def category_setup():
    """Une catégorie avec : une prestation jamais réservée (avec photo et vidéo) et une déjà réservée."""
    with SessionLocal() as db:
        h = make_hairdresser(db, "Marie", slots=tuple((d, 540, 1020) for d in range(7)))
        cat = make_category(db, "TEST Coiffure")
        fresh = make_service(db, "TEST Neuve", category=cat, price="40.00", kinds=("wash_basin",))
        old = make_service(db, "TEST Ancienne", category=cat)
        photo, video = make_media_file(db, fresh, "image", "p1"), make_media_file(db, fresh, "video", "v1")
        make_past(db, book(db, old, h))
        return {"cat": cat.id, "fresh": fresh.id, "old": old.id, "photo": media_path(photo), "video": media_path(video),
                "poster": Path(get_settings().media_dir) / video.poster_key}  # fmt: skip


def test_category_delete_page_warns_and_lists_consequences(admin):
    ids = category_setup()
    page = html.unescape(admin.get(f"/admin/categories/{ids['cat']}/delete").text)
    assert "tout le contenu de cette catégorie disparaîtra" in page and "toutes ses prestations" in page
    assert "Supprimées définitivement (1)" in page and "TEST Neuve" in page and "2 photo(s) ou vidéo(s)" in page
    assert "Archivées (1)" in page and "TEST Ancienne" in page and "l'historique des rendez-vous est conservé" in page
    assert "Télécharger la sauvegarde" in page and f"/admin/categories/{ids['cat']}/backup" in page  # sauvegarde proposée
    assert "écrivez le nom de la catégorie" in page


def test_category_backup_zip_contains_services_and_media_files(admin):
    ids = category_setup()
    r = admin.get(f"/admin/categories/{ids['cat']}/backup")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip" and r.headers["cache-control"] == "no-store"
    assert re.fullmatch(r'attachment; filename="kyja-sauvegarde-test-coiffure-\d{8}\.zip"', r.headers["content-disposition"])
    z = zipfile.ZipFile(io.BytesIO(r.content))
    names = z.namelist()
    assert "LISEZ-MOI.txt" in names and "categorie.json" in names
    data = json.loads(z.read("categorie.json"))
    assert data["category"]["name"] == "TEST Coiffure" and data["format_version"] == 1
    by_name = {s["name"]: s for s in data["services"]}
    assert set(by_name) == {"TEST Neuve", "TEST Ancienne"}
    neuve = by_name["TEST Neuve"]
    assert neuve["price"] == "40.00" and neuve["required_equipment"] == ["wash_basin"] and neuve["base_duration_min"] == 60
    files = {m["type"]: m for m in neuve["media"]}
    assert z.read(files["image"]["file"]) == ids["photo"].read_bytes()  # les vrais fichiers sont dans la sauvegarde
    assert z.read(files["video"]["file"]) == ids["video"].read_bytes() and z.read(files["video"]["poster"]) == ids["poster"].read_bytes()
    assert "@" not in z.read("categorie.json").decode()  # aucune donnée de cliente : ni email ni adresse
    assert "client_id" not in z.read("categorie.json").decode()


def test_category_backup_survives_a_missing_media_file(admin):
    ids = category_setup()
    ids["photo"].unlink()  # le fichier a disparu du stockage
    r = admin.get(f"/admin/categories/{ids['cat']}/backup")
    assert r.status_code == 200
    assert any(n.endswith(".MANQUANT") for n in zipfile.ZipFile(io.BytesIO(r.content)).namelist())


def test_category_delete_requires_exact_name_and_changes_nothing_otherwise(admin):
    ids = category_setup()
    for wrong in ("", "autre", "TEST Coif"):
        r = post(admin, f"/admin/categories/{ids['cat']}/delete", confirmation=wrong)
        assert r.status_code == 400 and "écrivez exactement le nom" in html.unescape(r.text)
    with SessionLocal() as db:
        assert db.get(Category, ids["cat"]) is not None and db.get(Service, ids["fresh"]) is not None
    assert ids["photo"].exists()


def test_category_delete_removes_unbooked_archives_booked_and_keeps_history(admin):
    ids = category_setup()
    r = post(admin, f"/admin/categories/{ids['cat']}/delete", confirmation="test coiffure")  # casse indifférente
    assert r.status_code == 303 and "removed=1" in r.headers["location"] and "archived=1" in r.headers["location"]
    with SessionLocal() as db:
        assert db.get(Category, ids["cat"]) is None  # la catégorie a disparu
        assert db.get(Service, ids["fresh"]) is None  # jamais réservée : effacée
        old = db.get(Service, ids["old"])
        assert old.archived_at is not None and old.is_active is False and old.category_id is None  # réservée : archivée
        assert db.scalar(select(Appointment).where(Appointment.service_id == ids["old"])) is not None  # historique intact
        assert db.scalars(select(Media).where(Media.service_id == ids["fresh"])).all() == []
    assert not ids["photo"].exists() and not ids["video"].exists() and not ids["poster"].exists()  # fichiers effacés
    assert "TEST Neuve" not in visitor().get("/services").text
    assert "1 prestation(s) effacée(s)" in html.unescape(admin.get(r.headers["location"]).text)
    assert "TEST Ancienne" in admin.get("/admin/appointments").text  # l'historique la nomme toujours


def test_category_with_upcoming_appointments_cannot_be_deleted(admin):
    with SessionLocal() as db:
        h = make_hairdresser(db, "Marie", slots=tuple((d, 540, 1020) for d in range(7)))
        cat = make_category(db, "TEST Coiffure")
        svc = make_service(db, "TEST Réservée bientôt", category=cat)
        book(db, svc, h)
        cid, sid = cat.id, svc.id
    page = html.unescape(admin.get(f"/admin/categories/{cid}/delete").text)
    assert "Suppression impossible" in page and "TEST Réservée bientôt (1)" in page
    assert 'name="confirmation"' not in page  # pas de formulaire de suppression
    r = post(admin, f"/admin/categories/{cid}/delete", confirmation="TEST Coiffure")
    assert r.status_code == 400 and "Annulez-les d'abord" in html.unescape(r.text)
    with SessionLocal() as db:
        assert db.get(Category, cid) is not None and db.get(Service, sid).archived_at is None


def test_empty_category_can_be_deleted_and_never_touches_other_categories(admin):
    with SessionLocal() as db:
        empty, other = make_category(db, "TEST Vide"), make_category(db, "TEST Autre")
        keep = make_service(db, "TEST Garde", category=other)
        eid, oid, kid = empty.id, other.id, keep.id
    page = html.unescape(admin.get(f"/admin/categories/{eid}/delete").text)
    assert "ne contient aucune prestation" in page
    assert post(admin, f"/admin/categories/{eid}/delete", confirmation="TEST Vide").status_code == 303
    with SessionLocal() as db:
        assert db.get(Category, eid) is None and db.get(Category, oid) is not None and db.get(Service, kid).category_id == oid


def test_deleting_a_category_never_deletes_the_builtin_request():
    with SessionLocal() as db:
        cat = make_category(db, "TEST Piège")
        builtin = engine.custom_request_service(db)
        builtin.category_id = cat.id  # cas anormal : la demande intégrée rangée dans une catégorie
        db.commit()
        from app.services import lifecycle

        with pytest.raises(lifecycle.Protected):
            lifecycle.remove_category(db, cat)
        db.rollback()
        builtin = engine.custom_request_service(db)
        builtin.category_id = None
        db.commit()
        assert db.get(Service, builtin.id) is not None


# ============================================================ supprimer une coiffeuse (archivage)


def hairdresser_setup(with_future=False):
    with SessionLocal() as db:
        keep = make_hairdresser(db, "Sonia", slots=tuple((d, 540, 1020) for d in range(7)))
        gone = make_hairdresser(db, "Marie", slots=tuple((d, 540, 1020) for d in range(7)))
        gone.user.phone = "514 555-0100"
        svc = make_service(db, "TEST Brushing")
        past = book(db, svc, gone, day_offset=0, hour=9)
        make_past(db, past)
        future_id = book(db, svc, gone, day_offset=1, hour=11).id if with_future else None
        return {"keep": keep.id, "gone": gone.id, "user": gone.user_id, "svc": svc.id, "past": past.id, "future": future_id}


def test_delete_hairdresser_keeps_history_and_contacts_but_hides_her(admin):
    ids = hairdresser_setup()
    page = html.unescape(admin.get(f"/admin/hairdressers/{ids['gone']}/delete").text)
    assert "disparaît de la réservation" in page and "Conservé" in page and "Ses coordonnées" in page
    r = post(admin, f"/admin/hairdressers/{ids['gone']}/delete")
    assert r.status_code == 303 and "removed=Marie" in r.headers["location"]
    with SessionLocal() as db:
        h = db.get(Hairdresser, ids["gone"])
        assert h.archived_at is not None and h.is_active is False  # archivée, pas supprimée
        u = db.get(User, ids["user"])
        assert (u.full_name, u.email, u.phone) == ("Marie", "marie@test.kyja", "514 555-0100") and u.is_active is False  # contacts gardés
        assert db.get(Appointment, ids["past"]).hairdresser_id == ids["gone"]  # l'historique la nomme encore
    # plus proposée nulle part
    c = visitor()
    register(c)
    assert "Marie" not in c.get(f"/bookings/new/{ids['svc']}").text and "Sonia" in c.get(f"/bookings/new/{ids['svc']}").text
    assert "Marie" not in admin.get("/admin/hairdressers").text and "Marie" in admin.get("/admin/hairdressers?archived=1").text
    assert "Marie" not in admin.get("/admin/appointments/new").text
    month = next_weekday(0).strftime("%Y-%m")
    assert c.get(f"/bookings/calendar?service_id={ids['svc']}&hairdresser={ids['gone']}&month={month}").text.count('name="day"') == 0
    # ses coordonnées restent visibles dans la liste des archivées ; l'historique des rendez-vous aussi
    assert "marie@test.kyja" in admin.get("/admin/hairdressers?archived=1").text and "Marie" in admin.get("/admin/appointments").text
    # et elle ne peut plus se connecter
    login_try = visitor()
    assert login_try.post("/auth/login", data={"email": "marie@test.kyja", "password": "hairdresser password 1", "csrf_token": csrf(login_try)}).status_code == 401


def test_delete_hairdresser_with_upcoming_appointments_needs_explicit_cancellation(admin):
    ids = hairdresser_setup(with_future=True)
    page = html.unescape(admin.get(f"/admin/hairdressers/{ids['gone']}/delete").text)
    assert "1 rendez-vous à venir" in page and 'name="cancel_future"' in page
    r = post(admin, f"/admin/hairdressers/{ids['gone']}/delete")  # case non cochée : refusé
    assert r.status_code == 400 and "Cochez la case" in html.unescape(r.text)
    with SessionLocal() as db:
        assert db.get(Hairdresser, ids["gone"]).archived_at is None
        assert db.get(Appointment, ids["future"]).status == AppointmentStatus.confirmed
    r = post(admin, f"/admin/hairdressers/{ids['gone']}/delete", cancel_future="true")
    assert r.status_code == 303 and "cancelled=1" in r.headers["location"]
    with SessionLocal() as db:
        assert db.get(Appointment, ids["future"]).status == AppointmentStatus.cancelled
        assert db.get(Appointment, ids["past"]).status == AppointmentStatus.confirmed  # le passé n'est pas touché
    assert "1 rendez-vous à venir annulé" in html.unescape(admin.get(r.headers["location"]).text)


def test_restore_hairdresser_brings_her_back(admin):
    ids = hairdresser_setup()
    post(admin, f"/admin/hairdressers/{ids['gone']}/delete")
    assert "archivée" in html.unescape(admin.get(f"/admin/hairdressers/{ids['gone']}").text)
    assert post(admin, f"/admin/hairdressers/{ids['gone']}/restore").status_code == 303
    with SessionLocal() as db:
        h = db.get(Hairdresser, ids["gone"])
        assert h.archived_at is None and h.is_active and db.get(User, ids["user"]).is_active
    c = visitor()
    register(c)
    assert "Marie" in c.get(f"/bookings/new/{ids['svc']}").text
    again = visitor()
    assert again.post("/auth/login", data={"email": "marie@test.kyja", "password": "hairdresser password 1", "csrf_token": csrf(again)}).status_code == 303


def test_hairdresser_removal_is_admin_only(admin):
    ids = hairdresser_setup()
    c = visitor()
    register(c)
    assert c.get(f"/admin/hairdressers/{ids['gone']}/delete").status_code == 403
    assert post(c, f"/admin/hairdressers/{ids['gone']}/delete").status_code == 403
    assert post(c, f"/admin/hairdressers/{ids['gone']}/restore").status_code == 403
    assert admin.post(f"/admin/hairdressers/{ids['gone']}/delete").status_code == 403  # sans CSRF


def test_archived_hairdresser_is_not_a_booking_candidate_for_any(admin):
    ids = hairdresser_setup()
    post(admin, f"/admin/hairdressers/{ids['gone']}/delete")
    with SessionLocal() as db:
        svc, client = db.get(Service, ids["svc"]), make_client_user(db, "x@test.kyja")
        a = engine.create_booking(db, client=client, service=svc, hairdresser_id=None, start=local_start(next_weekday(0) + timedelta(days=2), 10))
        assert a.hairdresser_id == ids["keep"]  # « peu importe » ne choisit jamais une coiffeuse archivée
        with pytest.raises(engine.BookingError):
            engine.create_booking(db, client=client, service=svc, hairdresser_id=ids["gone"], start=local_start(next_weekday(0) + timedelta(days=3), 10))


# ============================================================ stock faible


@pytest.mark.parametrize(
    "stock,expected",
    [(0, "Rupture de stock"), (1, "En stock (1)"), (3, "En stock (3)"), (5, "En stock (5)"), (6, "En stock"), (40, "En stock")],
)
def test_product_page_shows_remaining_stock_when_low(stock, expected):
    with SessionLocal() as db:
        p = Product(name="TEST Huile", price=20, stock=stock)
        db.add(p)
        db.commit()
        pid = p.id
    page = html.unescape(visitor().get(f"/products/{pid}").text)
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    assert expected in text
    low = 1 <= stock <= 5
    assert ("data-low-stock" in page) == low  # le nombre n'apparaît qu'à 5 ou moins
    if low:
        assert re.search(r'<span class="[^"]*text-danger[^"]*" data-low-stock>\(' + str(stock) + r"\)</span>", page)  # en rouge
    if stock > 5:
        assert "En stock (" not in text
    if stock == 0:
        assert "En stock" not in text


# ============================================================ avis « prix estimatif »


def slot_data(svc_id, h_id):
    return {"service_id": svc_id, "hairdresser": str(h_id), "slot": local_start(next_weekday(0), 10).isoformat()}


def test_review_shows_price_notice_and_dialog_for_catalog_booking_when_logged_in():
    with SessionLocal() as db:
        h = make_hairdresser(db, "Marie", slots=tuple((d, 540, 1020) for d in range(7)))
        svc = make_service(db, "TEST Brushing", price="55.00")
    c = visitor()
    register(c)
    page = c.post("/bookings/review", data={**slot_data(svc.id, h.id), "csrf_token": csrf(c)}).text
    text = html.unescape(page)
    assert 'class="price-notice"' in page and "Prix estimatif" in text and "analyse des photos et des vidéos" in text
    assert '<dialog id="price-dialog"' in page and "Le prix est estimatif" in text and "J'ai compris, confirmer" in text
    assert 'data-price-dialog="price-dialog"' in page and "/static/js/confirm-dialog.js" in page
    assert "55.00 $ CAD" in text  # le prix estimatif figure dans la fenêtre


def test_visitor_sees_the_notice_in_the_recap_but_no_dialog_yet():
    with SessionLocal() as db:
        h = make_hairdresser(db, "Marie", slots=tuple((d, 540, 1020) for d in range(7)))
        svc = make_service(db, "TEST Brushing")
    v = visitor()
    page = v.post("/bookings/review", data={**slot_data(svc.id, h.id), "csrf_token": csrf(v)}).text
    assert 'class="price-notice"' in page and "<dialog" not in page  # la fenêtre viendra à la confirmation


def test_custom_request_has_no_price_notice_or_dialog():
    with SessionLocal() as db:
        h = make_hairdresser(db, "Marie", slots=tuple((d, 540, 1020) for d in range(7)))
        bid = engine.custom_request_service(db).id
    c = visitor()
    register(c)
    page = c.post("/bookings/review", data={**slot_data(bid, h.id), "notes": "Tresses africaines avec rajouts, longueur taille", "csrf_token": csrf(c)}).text
    assert "price-notice" not in page and "<dialog" not in page and "confirm-dialog.js" not in page


def test_confirmed_catalog_appointment_repeats_the_notice_until_final_price():
    with SessionLocal() as db:
        h = make_hairdresser(db, "Marie", slots=tuple((d, 540, 1020) for d in range(7)))
        svc = make_service(db, "TEST Brushing")
    c = visitor()
    register(c)
    r = c.post("/bookings", data={"service_id": svc.id, "hairdresser": str(h.id), "start": local_start(next_weekday(0), 10).isoformat(), "csrf_token": csrf(c)})
    detail = r.headers["location"]
    page = c.get(detail).text
    assert "price-notice" in page and "analyse des photos et des vidéos" in html.unescape(page)
    appt_id = int(re.search(r"/bookings/(\d+)", detail).group(1))
    with SessionLocal() as db:  # le prix final est fixé : l'avis n'a plus lieu d'être
        db.execute(update(Appointment).where(Appointment.id == appt_id).values(final_price=60))
        db.commit()
    assert "price-notice" not in c.get(detail).text
