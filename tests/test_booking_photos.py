"""Photos d'inspiration des demandes sur mesure (PRD 4.A mode 2) : jusqu'à 10, privées, nettoyées."""
import io
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import delete, select, update

from app.bookings import photos
from app.config import get_settings
from app.db.base import SessionLocal
from app.db.models.core import Appointment, BookingPhoto, PricingMode, User
from app.main import app
from app.media.storage import get_private_storage
from tests.conftest import csrf, local_start, make_admin, make_hairdresser, make_service, next_weekday, register

NOTES = "Tresses africaines avec rajouts, longueur milieu du dos"


def png(size=(80, 60), color=(190, 40, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


def jpeg_with_gps() -> bytes:
    im = Image.new("RGB", (300, 200), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "ACME Phone"  # fabricant
    exif[0x8825] = {1: "N", 2: (45.0, 30.0, 0.0), 3: "W", 4: (73.0, 35.0, 0.0)}  # GPS : coordonnées du domicile
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=exif)
    return buf.getvalue()


@pytest.fixture
def setup():
    with SessionLocal() as db:
        h = make_hairdresser(db, "Marie", "1.00")
        custom = make_service(db, "TEST Sur mesure", base=120, mode=PricingMode.quote)
        catalog = make_service(db, "TEST Brushing", base=60, price="55.00")
        return {"h": h.id, "hid_user": h.user_id, "custom": custom.id, "catalog": catalog.id}


def other_client():
    c = TestClient(app, follow_redirects=False)
    c.get("/auth/login")
    return c


def up(c, content, name="p.png", ctype="image/png", with_csrf=True):
    headers = {"x-csrf-token": csrf(c)} if with_csrf else {}
    return c.post("/bookings/photos", files={"file": (name, content, ctype)}, headers=headers)


def all_photos():
    with SessionLocal() as db:
        return db.scalars(select(BookingPhoto).order_by(BookingPhoto.id)).all()


# --- envoi ------------------------------------------------------------------------------------


def test_upload_requires_login_and_csrf(client):
    assert up(client, png()).status_code == 401
    register(client)
    assert up(client, png(), with_csrf=False).status_code == 403
    r = up(client, png())
    assert r.status_code == 200 and r.json()["url"] == f"/bookings/photos/{r.json()['id']}"


def test_upload_is_stored_privately_and_never_public(client):
    register(client)
    pid = up(client, png()).json()["id"]
    photo = all_photos()[0]
    assert (Path(get_settings().private_dir) / photo.storage_key).exists()
    assert not (Path(get_settings().media_dir) / photo.storage_key).exists()
    assert client.get(f"/media-files/{photo.storage_key}").status_code == 404  # jamais servie par le dossier public
    r = client.get(f"/bookings/photos/{pid}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert r.headers["cache-control"].startswith("private")


def test_exif_and_gps_are_stripped_and_big_images_resized(client):
    register(client)
    pid = up(client, jpeg_with_gps(), "gps.jpg", "image/jpeg").json()["id"]
    stored = Image.open(io.BytesIO(client.get(f"/bookings/photos/{pid}").content))
    assert stored.format == "WEBP" and not stored.getexif() and "exif" not in stored.info
    big = up(client, png((4000, 2500)), "big.png").json()
    assert max(big["width"], big["height"]) == 1600


@pytest.mark.parametrize(
    "content,name,ctype",
    [
        (b"<?php system($_GET['c']); ?>", "evil.jpg", "image/jpeg"),  # faux JPEG
        (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;", "a.gif", "image/gif"),
        (b"%PDF-1.4 fake", "doc.png", "image/png"),
        (b"\x89PNG\r\n\x1a\n" + b"truncated", "bad.png", "image/png"),  # signature PNG mais corrompu
        (b"", "empty.png", "image/png"),
    ],
)
def test_invalid_files_rejected(client, content, name, ctype):
    register(client)
    r = up(client, content, name, ctype)
    assert r.status_code == 400 and r.json()["detail"]
    assert all_photos() == []


def test_max_ten_photos(client):
    register(client)
    for i in range(10):
        assert up(client, png(color=(i * 20, 50, 50))).status_code == 200
    r = up(client, png())
    assert r.status_code == 400 and "Maximum 10" in r.json()["detail"]
    assert len(all_photos()) == 10


def test_file_size_limit(client, monkeypatch):
    register(client)
    monkeypatch.setattr(get_settings(), "image_max_mb", 1)
    buf = io.BytesIO()
    Image.frombytes("RGB", (700, 700), os.urandom(700 * 700 * 3)).save(buf, "PNG")  # bruit : ~1,5 Mo, image valide
    assert len(buf.getvalue()) > 2**20
    r = up(client, buf.getvalue())
    assert r.status_code == 400 and "trop volumineuse" in r.json()["detail"]
    assert all_photos() == []
    assert up(client, png()).status_code == 200  # une image sous la limite passe


def test_upload_rate_limit(client):
    register(client)
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.email == "a@test.kyja"))
    for _ in range(photos.upload_limiter.max_hits):
        photos.upload_limiter.hit(f"user:{uid}")
    r = up(client, png())
    assert r.status_code == 400 and "Trop de photos" in r.json()["detail"]


# --- visibilité ---------------------------------------------------------------------------------


def test_only_owner_and_admin_see_a_draft_photo(client):
    register(client)
    pid = up(client, png()).json()["id"]
    other = other_client()
    register(other, email="b@test.kyja")
    assert other.get(f"/bookings/photos/{pid}").status_code == 404
    assert other_client().get(f"/bookings/photos/{pid}").status_code == 401
    admin = other_client()
    make_admin(admin)
    assert admin.get(f"/bookings/photos/{pid}").status_code == 200


def test_assigned_hairdresser_sees_photo_only_after_booking(client, setup):
    register(client)
    pid = up(client, png()).json()["id"]
    from app.auth.security import hash_password
    with SessionLocal() as db:
        db.get(User, setup["hid_user"]).password_hash = hash_password("hairdresser password 1")
        db.commit()
    marie = other_client()
    assert marie.post("/auth/login", data={"email": "marie@test.kyja", "password": "hairdresser password 1", "csrf_token": csrf(marie)}).status_code == 303
    assert marie.get(f"/bookings/photos/{pid}").status_code == 404  # brouillon : pas encore rattaché
    start = local_start(next_weekday(0), 10)
    r = client.post("/bookings", data={"service_id": setup["custom"], "hairdresser": setup["h"], "start": start.isoformat(),
                                       "notes": NOTES, "photo_ids": [pid], "csrf_token": csrf(client)})  # fmt: skip
    assert r.status_code == 303
    assert marie.get(f"/bookings/photos/{pid}").status_code == 200  # rattachée à son rendez-vous
    with SessionLocal() as db:
        h2 = make_hairdresser(db, "Autre", email="autre@test.kyja")
        db.get(User, h2.user_id).password_hash = hash_password("hairdresser password 1")
        db.commit()
    autre = other_client()
    autre.post("/auth/login", data={"email": "autre@test.kyja", "password": "hairdresser password 1", "csrf_token": csrf(autre)})
    assert autre.get(f"/bookings/photos/{pid}").status_code == 404  # une autre coiffeuse ne la voit pas


# --- brouillons --------------------------------------------------------------------------------


def test_delete_draft_by_owner_only(client):
    register(client)
    pid = up(client, png()).json()["id"]
    other = other_client()
    register(other, email="b@test.kyja")
    assert other.post(f"/bookings/photos/{pid}/delete", headers={"x-csrf-token": csrf(other)}).status_code == 404
    path = Path(get_settings().private_dir) / all_photos()[0].storage_key
    assert path.exists()
    assert client.post(f"/bookings/photos/{pid}/delete", headers={"x-csrf-token": csrf(client)}).json() == {"status": "ok"}
    assert all_photos() == [] and not path.exists()  # ligne ET fichier supprimés
    assert client.post(f"/bookings/photos/{pid}/delete", headers={"x-csrf-token": csrf(client)}).status_code == 404


def test_stale_drafts_are_purged_with_their_files(client):
    register(client)
    up(client, png())
    up(client, png(color=(1, 2, 3)))
    old = all_photos()[0]
    old_path = Path(get_settings().private_dir) / old.storage_key
    with SessionLocal() as db:
        db.execute(update(BookingPhoto).where(BookingPhoto.id == old.id).values(created_at=datetime.now(timezone.utc) - timedelta(hours=25)))
        db.commit()
    assert old_path.exists()
    up(client, png(color=(9, 9, 9)))  # un nouvel envoi déclenche la purge
    ids = [p.id for p in all_photos()]
    assert old.id not in ids and len(ids) == 2 and not old_path.exists()


# --- parcours ---------------------------------------------------------------------------------------


def test_drafts_are_listed_on_custom_page_only(client, setup):
    register(client)
    pid = up(client, png()).json()["id"]
    custom = client.get(f"/bookings/new/{setup['custom']}").text
    assert f'name="photo_ids" value="{pid}"' in custom and "0 / 10 photos" in custom and 'id="photo-input"' in custom
    assert 'data-max="10"' in custom and "booking-photos.js" in custom
    catalog = client.get(f"/bookings/new/{setup['catalog']}").text
    assert "photo-input" not in catalog and "booking-photos.js" not in catalog  # mode 1 : pas de photos
    assert "Précisions (facultatif)" in catalog


def test_review_confirm_attaches_photos_and_detail_shows_them(client, setup):
    register(client)
    ids = [up(client, png(color=(i * 40, 90, 120))).json()["id"] for i in range(3)]
    start = local_start(next_weekday(0), 10)
    base = {"service_id": setup["custom"], "hairdresser": setup["h"], "notes": NOTES, "csrf_token": csrf(client)}
    rv = client.post("/bookings/review", data={**base, "slot": start.isoformat(), "photo_ids": ids})
    assert rv.status_code == 200 and "Photos d'inspiration (3)" in rv.text
    assert all(f'name="photo_ids" value="{i}"' in rv.text for i in ids) and 'formaction="/bookings/edit"' in rv.text
    r = client.post("/bookings", data={**base, "start": start.isoformat(), "photo_ids": ids})
    assert r.status_code == 303
    detail = client.get(r.headers["location"]).text
    assert "Photos d'inspiration (3)" in detail and all(f"/bookings/photos/{i}" in detail for i in ids)
    with SessionLocal() as db:
        appt = db.scalar(select(Appointment))
        assert sorted(p.id for p in appt.photos) == sorted(ids) and photos.drafts(db, appt.client_id) == []
    other = other_client()
    register(other, email="b@test.kyja")
    assert other.get(r.headers["location"].split("?")[0]).status_code == 404


def test_cannot_use_someone_elses_photo_or_more_than_ten(client, setup):
    other = other_client()
    register(other, email="b@test.kyja")
    foreign = up(other, png()).json()["id"]
    register(client)
    mine = up(client, png()).json()["id"]
    start = local_start(next_weekday(0), 10)
    base = {"service_id": setup["custom"], "hairdresser": setup["h"], "notes": NOTES, "csrf_token": csrf(client)}
    bad = client.post("/bookings/review", data={**base, "slot": start.isoformat(), "photo_ids": [mine, foreign]})
    assert bad.status_code == 400 and "n'est plus disponible" in bad.text.replace("&#39;", "'")
    too_many = client.post("/bookings/review", data={**base, "slot": start.isoformat(), "photo_ids": list(range(1, 12))})
    assert too_many.status_code == 400 and "Maximum 10" in too_many.text
    forced = client.post("/bookings", data={**base, "start": start.isoformat(), "photo_ids": [foreign]})
    assert forced.status_code == 400  # même en forçant la confirmation
    with SessionLocal() as db:
        assert db.scalars(select(Appointment)).all() == []  # aucun rendez-vous créé
        assert db.get(BookingPhoto, foreign).appointment_id is None  # la photo d'autrui n'a pas été volée


def test_photos_are_ignored_in_catalog_mode(client, setup):
    register(client)
    pid = up(client, png()).json()["id"]
    start = local_start(next_weekday(0), 10)
    r = client.post("/bookings", data={"service_id": setup["catalog"], "hairdresser": setup["h"], "start": start.isoformat(),
                                       "photo_ids": [pid], "csrf_token": csrf(client)})  # fmt: skip
    assert r.status_code == 303
    with SessionLocal() as db:
        assert db.get(BookingPhoto, pid).appointment_id is None  # mode 1 : photos non rattachées


def test_edit_keeps_notes_hairdresser_and_photos(client, setup):
    register(client)
    pid = up(client, png()).json()["id"]
    r = client.post("/bookings/edit", data={"service_id": setup["custom"], "hairdresser": setup["h"], "notes": NOTES,
                                            "csrf_token": csrf(client)})  # fmt: skip
    assert r.status_code == 200
    assert NOTES in r.text and f'name="photo_ids" value="{pid}"' in r.text
    assert f'value="{setup["h"]}" checked' in r.text


def test_photos_deleted_with_their_appointment(client, setup):
    register(client)
    pid = up(client, png()).json()["id"]
    start = local_start(next_weekday(0), 10)
    r = client.post("/bookings", data={"service_id": setup["custom"], "hairdresser": setup["h"], "start": start.isoformat(),
                                       "notes": NOTES, "photo_ids": [pid], "csrf_token": csrf(client)})  # fmt: skip
    assert r.status_code == 303
    key = all_photos()[0].storage_key
    with SessionLocal() as db:
        db.execute(delete(Appointment))  # ON DELETE CASCADE : les lignes de photos suivent
        db.commit()
        assert db.get(BookingPhoto, pid) is None
    # La base ne supprime que les lignes : le fichier doit être effacé par l'application.
    path = Path(get_settings().private_dir) / key
    assert path.exists()
    get_private_storage().delete(key)
    assert not path.exists()
