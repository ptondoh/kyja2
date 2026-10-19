import io
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote

import pytest
from PIL import Image
from sqlalchemy import select

from app.config import get_settings
from app.db.base import SessionLocal
from app.db.models.core import Media, MediaStatus, Product, Service
from app.db.models.settings import get_int_setting
from app.media.probe import find_ffmpeg
from tests.conftest import csrf, make_admin, register


def png_bytes(size=(64, 48), color=(200, 30, 90)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, "PNG")
    return buf.getvalue()


@pytest.fixture(scope="session")
def video_file(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("vid") / "clip.mp4"
    subprocess.run(
        [find_ffmpeg(), "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=15",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)],
        capture_output=True, check=True,
    )  # fmt: skip
    return out


@pytest.fixture
def admin(client):
    make_admin(client)
    return client


def create_service(c, name="TEST Brushing", **over):
    data = {"name": name, "category": "coiffure", "description": "Brushing", "base_duration_min": "45",
            "buffer_min": "10", "price": "55.00", "is_active": "true", "csrf_token": csrf(c)}  # fmt: skip
    data.update(over)
    return c.post("/admin/services", data=data)


def service_id(name="TEST Brushing") -> int:
    with SessionLocal() as db:
        return db.scalar(select(Service.id).where(Service.name == name))


def upload(c, path_or_bytes, owner="services", owner_id=None, filename="f.bin", ctype="application/octet-stream"):
    content = path_or_bytes.read_bytes() if isinstance(path_or_bytes, Path) else path_or_bytes
    return c.post(
        f"/admin/{owner}/{owner_id}/media",
        data={"csrf_token": csrf(c)},
        files={"file": (filename, content, ctype)},
    )


# --- accès ------------------------------------------------------------------


def test_admin_requires_login_and_role(client):
    assert client.get("/admin").status_code == 401
    register(client)  # simple cliente
    assert client.get("/admin").status_code == 403
    assert client.get("/admin/services").status_code == 403
    r = client.post("/admin/services", data={"name": "TEST x", "csrf_token": csrf(client)})
    assert r.status_code == 403


def test_admin_post_requires_csrf(admin):
    r = admin.post("/admin/services", data={"name": "TEST x"})
    assert r.status_code == 403


# --- CRUD -------------------------------------------------------------------


def test_service_create_edit_and_public_listing(admin, client):
    r = create_service(admin)
    assert r.status_code == 303
    sid = service_id()
    assert admin.get(f"/admin/services/{sid}").status_code == 200

    pub = client.get("/services")
    assert "TEST Brushing" in pub.text and "55.00" in pub.text
    assert "TEST Brushing" in client.get(f"/services/{sid}").text
    assert "Paiement au salon" in client.get(f"/services/{sid}").text

    admin.post(f"/admin/services/{sid}", data={"name": "TEST Brushing", "base_duration_min": "45", "price": "60.00",
               "csrf_token": csrf(admin)})  # is_active absent => masqué  # fmt: skip
    assert client.get(f"/services/{sid}").status_code == 404
    assert "TEST Brushing" not in client.get("/services").text


def test_service_validation_errors(admin):
    r = create_service(admin, price="abc", base_duration_min="1")
    assert r.status_code == 400
    assert "Prix estimatif invalide" in r.text and "Durée doit être" in r.text
    assert service_id() is None
    assert create_service(admin, price="-5").status_code == 400
    assert create_service(admin, name="  ").status_code == 400


def test_html_in_name_is_escaped(admin, client):
    create_service(admin, name="TEST <script>alert(1)</script>")
    body = client.get("/services").text
    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


def test_product_create_and_public(admin, client):
    r = admin.post("/admin/products", data={"name": "TEST Shampoing", "price": "19,99", "stock": "5",
                   "is_active": "true", "csrf_token": csrf(admin)})  # fmt: skip
    assert r.status_code == 303
    assert "TEST Shampoing" in client.get("/products").text
    assert admin.post("/admin/products", data={"name": "TEST Bad", "price": "1", "stock": "-1",
                      "csrf_token": csrf(admin)}).status_code == 400  # fmt: skip


# --- médias -----------------------------------------------------------------


def test_image_upload_is_optimized_and_published(admin, client):
    create_service(admin)
    sid = service_id()
    r = upload(admin, png_bytes((3000, 2000)), owner_id=sid, filename="photo.png", ctype="image/png")
    assert r.status_code == 303 and "uploaded=1" in r.headers["location"]
    with SessionLocal() as db:
        m = db.scalar(select(Media).where(Media.service_id == sid))
    assert m.status == MediaStatus.ready
    assert m.mime_type == "image/webp" and max(m.width, m.height) == 1600
    assert client.get(m.url).status_code == 200
    page = client.get(f"/services/{sid}").text
    assert m.url in page


def test_video_upload_transcoded_with_poster(admin, client, video_file):
    create_service(admin)
    sid = service_id()
    upload(admin, video_file, owner_id=sid, filename="clip.mp4", ctype="video/mp4")
    with SessionLocal() as db:
        m = db.scalar(select(Media).where(Media.service_id == sid))
    assert m.status == MediaStatus.ready, m.error
    assert m.mime_type == "video/mp4" and m.poster_url and m.duration_s in (2, 3)
    assert client.get(m.url).status_code == 200 and client.get(m.poster_url).status_code == 200
    page = client.get(f"/services/{sid}").text
    assert 'preload="none"' in page and m.poster_url in page


def test_processing_media_not_public(admin, client):
    create_service(admin)
    sid = service_id()
    with SessionLocal() as db:
        db.add(Media(service_id=sid, type="image", status=MediaStatus.processing, url="/media-files/x.webp"))
        db.commit()
    assert "/media-files/x.webp" not in client.get(f"/services/{sid}").text


def test_fake_extension_rejected_by_content(admin):
    create_service(admin)
    sid = service_id()
    r = upload(admin, b"<?php echo 1; ?>", owner_id=sid, filename="evil.jpg", ctype="image/jpeg")
    assert "Type de fichier non autorisé" in unquote(r.headers["location"])
    r = upload(admin, b"\x89PNG\r\n\x1a\nnot really", owner_id=sid, filename="x.png", ctype="image/png")
    assert "upload_error" in r.headers["location"]
    with SessionLocal() as db:
        assert db.scalars(select(Media).where(Media.service_id == sid)).all() == []


def test_video_too_long_rejected_and_limit_editable(admin, video_file):
    create_service(admin)
    sid = service_id()
    r = admin.post("/admin/settings", data={"video_max_mb": "100", "video_max_seconds": "10", "csrf_token": csrf(admin)})
    assert r.status_code == 303
    with SessionLocal() as db:
        assert get_int_setting(db, "video_max_seconds") == 10
    assert "upload_error" not in upload(admin, video_file, owner_id=sid, filename="c.mp4", ctype="video/mp4").headers["location"]
    # le clip de test dure 3 s : on abaisse le plafond sous cette durée via la base (minimum UI = 10 s)
    with SessionLocal() as db:
        from app.db.models.settings import set_setting

        set_setting(db, "video_max_seconds", "2")
    r = upload(admin, video_file, owner_id=sid, filename="c.mp4", ctype="video/mp4")
    assert "trop longue" in unquote(r.headers["location"])


def test_video_too_big_rejected(admin, video_file):
    create_service(admin)
    sid = service_id()
    from app.db.models.settings import set_setting

    with SessionLocal() as db:
        set_setting(db, "video_max_mb", "1")
    big = video_file.read_bytes() + b"\0" * (2 * 2**20)
    r = upload(admin, big, owner_id=sid, filename="big.mp4", ctype="video/mp4")
    assert "trop volumineuse" in unquote(r.headers["location"])


def test_settings_validation(admin):
    r = admin.post("/admin/settings", data={"video_max_mb": "0", "video_max_seconds": "5", "csrf_token": csrf(admin)})
    assert r.status_code == 400


def test_delete_media_removes_files(admin):
    create_service(admin)
    sid = service_id()
    upload(admin, png_bytes(), owner_id=sid, filename="p.png", ctype="image/png")
    with SessionLocal() as db:
        m = db.scalar(select(Media).where(Media.service_id == sid))
    f = Path(get_settings().media_dir) / m.storage_key
    assert f.exists()
    assert admin.post(f"/admin/media/{m.id}/delete", data={"csrf_token": csrf(admin)}).status_code == 303
    assert not f.exists()


def test_no_temp_files_left_behind(admin):
    create_service(admin)
    sid = service_id()
    upload(admin, png_bytes(), owner_id=sid, filename="p.png", ctype="image/png")
    upload(admin, b"garbage", owner_id=sid, filename="g.png", ctype="image/png")
    tmp = Path(get_settings().upload_tmp_dir)
    assert not any(tmp.glob("*.upload")) and not any(tmp.glob("work-*"))


def test_public_pages_have_no_admin_link_for_anonymous(client):
    assert 'href="/admin"' not in client.get("/services").text
    assert re.search(r"Prestations", client.get("/").text)
