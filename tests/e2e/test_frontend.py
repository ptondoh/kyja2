import re
import subprocess
from pathlib import Path

import pytest
from PIL import Image
from playwright.sync_api import expect

from app.auth.security import hash_password
from app.db.base import SessionLocal
from app.db.models.core import Role, User
from app.media.probe import find_ffmpeg

ADMIN_EMAIL, ADMIN_PASSWORD = "e2e-admin@test.kyja", "admin password 123"


@pytest.fixture(scope="session")
def media_files(tmp_path_factory):
    d = tmp_path_factory.mktemp("e2e-media")
    img = d / "coiffure.png"
    Image.new("RGB", (1200, 800), (176, 138, 91)).save(img)
    vid = d / "demo.mp4"
    subprocess.run(
        [find_ffmpeg(), "-y", "-f", "lavfi", "-i", "testsrc=duration=3:size=320x240:rate=15",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vid)],
        capture_output=True, check=True,
    )  # fmt: skip
    return img, vid


@pytest.fixture
def admin_user():
    with SessionLocal() as db:
        db.add(User(email=ADMIN_EMAIL, password_hash=hash_password(ADMIN_PASSWORD),
                    role=Role.admin, full_name="Admin E2E"))  # fmt: skip
        db.commit()


def login(page, email, password):
    page.goto("/auth/login")
    page.fill("input[name=email]", email)
    page.fill("input[name=password]", password)
    page.click("button[type=submit]")


def test_public_pages_render(page, shot):
    bad = []
    page.on("response", lambda r: bad.append((r.status, r.url)) if r.status >= 400 else None)
    page.goto("/")
    expect(page.locator("h1")).to_contain_text("KYJA")
    page.click("text=Prestations")
    expect(page).to_have_url(re.compile(r"/services$"))
    expect(page.locator("h1")).to_have_text("Nos prestations")
    page.click("header >> text=Produits")
    expect(page.locator("h1")).to_have_text("Nos produits")
    shot("01-produits-vide")
    assert bad == []
    assert page.console_errors == []


def test_register_logout_login_flow(page, shot):
    page.goto("/auth/register")
    page.fill("input[name=full_name]", "Marie Test")
    page.fill("input[name=email]", "marie@test.kyja")
    page.fill("input[name=password]", "un mot de passe solide")
    page.check("input[name=accept_terms]")
    shot("02-inscription")
    page.click("button[type=submit]")
    expect(page.locator("header")).to_contain_text("Marie Test")

    page.click("text=Déconnexion")
    expect(page.locator("header")).to_contain_text("Connexion")

    login(page, "marie@test.kyja", "un mot de passe solide")
    expect(page.locator("header")).to_contain_text("Marie Test")
    assert page.console_errors == []


def test_login_wrong_password_and_validation(page, shot):
    login(page, "personne@test.kyja", "mauvais mot de passe")
    expect(page.locator(".error")).to_have_text("Email ou mot de passe incorrect.")
    shot("03-connexion-erreur")

    page.goto("/auth/register")
    page.fill("input[name=full_name]", "X")
    page.fill("input[name=email]", "x@test.kyja")
    page.fill("input[name=password]", "court")
    page.check("input[name=accept_terms]")
    page.click("button[type=submit]")  # bloqué côté navigateur (minlength) : on reste sur la page
    expect(page).to_have_url(re.compile(r"/auth/register$"))


def test_cliente_cannot_open_admin(page):
    page.goto("/auth/register")
    page.fill("input[name=full_name]", "Cliente")
    page.fill("input[name=email]", "cliente@test.kyja")
    page.fill("input[name=password]", "un mot de passe solide")
    page.check("input[name=accept_terms]")
    page.click("button[type=submit]")
    assert page.locator("header >> text=Admin").count() == 0
    resp = page.goto("/admin")
    assert resp.status == 403


def test_admin_full_catalog_flow(page, shot, admin_user, media_files):
    img, vid = media_files
    login(page, ADMIN_EMAIL, ADMIN_PASSWORD)
    page.click("header >> text=Admin")
    expect(page.locator("h1")).to_have_text("Administration")

    # création d'une prestation
    page.goto("/admin/services/new")
    page.fill("input[name=name]", "TEST Coloration")
    page.fill("input[name=category]", "coiffure")
    page.fill("textarea[name=description]", "Coloration complète <b>sans ammoniaque</b>")
    page.fill("input[name=base_duration_min]", "90")
    page.fill("input[name=price]", "120.00")
    page.click("form.card >> button[type=submit]")
    expect(page.locator(".ok").first).to_have_text("Enregistré.")
    shot("04-admin-prestation")

    # téléversement image puis vidéo
    page.set_input_files("input[name=file]", str(img))
    page.click("text=Téléverser")
    expect(page.locator(".ok").first).to_contain_text("Fichier reçu")
    expect(page.locator(".media-list img")).to_have_count(1, timeout=20000)

    page.set_input_files("input[name=file]", str(vid))
    page.click("text=Téléverser")
    expect(page.locator(".media-list .badge")).to_contain_text("Vidéo", timeout=30000)
    expect(page.locator(".media-list li")).to_have_count(2)
    shot("05-admin-medias")

    # fichier interdit : message d'erreur, rien d'ajouté
    bad = img.parent / "faux.jpg"
    bad.write_bytes(b"<?php system('id'); ?>")
    page.set_input_files("input[name=file]", str(bad))
    page.click("text=Téléverser")
    expect(page.locator(".error")).to_contain_text("Type de fichier non autorisé")
    expect(page.locator(".media-list li")).to_have_count(2)

    # côté public
    page.goto("/services")
    tile = page.locator(".tile", has_text="TEST Coloration")
    expect(tile).to_be_visible()
    expect(tile.locator("img")).to_have_count(1)
    shot("06-public-tuiles")
    tile.click()
    expect(page.locator("h1")).to_have_text("TEST Coloration")
    expect(page.locator(".price")).to_contain_text("120.00 $ CAD")
    expect(page.locator(".desc")).to_contain_text("<b>sans ammoniaque</b>")  # échappé, pas interprété
    assert page.locator(".desc b").count() == 0
    expect(page.locator(".gallery img")).to_have_count(1)
    video = page.locator(".gallery video")
    expect(video).to_have_count(1)
    assert video.get_attribute("preload") == "none"
    assert video.get_attribute("poster")
    shot("07-public-detail")

    # la vidéo se lit vraiment
    played = page.evaluate(
        """async () => { const v = document.querySelector('video'); v.muted = true;
           await v.play(); await new Promise(r => setTimeout(r, 800));
           return v.currentTime > 0 && !v.error; }"""
    )
    assert played
    assert page.console_errors == [], page.console_errors


def test_admin_settings_page(page, admin_user):
    login(page, ADMIN_EMAIL, ADMIN_PASSWORD)
    page.goto("/admin/settings")
    expect(page.locator("input[name=video_max_mb]")).to_have_value("100")
    expect(page.locator("input[name=video_max_seconds]")).to_have_value("180")
    page.fill("input[name=video_max_mb]", "50")
    page.click("form.card >> button[type=submit]")
    expect(page.locator(".ok")).to_have_text("Enregistré.")
    expect(page.locator("input[name=video_max_mb]")).to_have_value("50")


def test_csp_blocks_inline_script_and_headers(page, base_url):
    page.goto("/")
    page.evaluate(
        """() => { const s = document.createElement('script');
           s.textContent = 'window.__pwned = 1'; document.body.appendChild(s); }"""
    )
    assert page.evaluate("window.__pwned") is None  # CSP : script inline sans nonce refusé
    assert any("Content Security Policy" in e for e in page.console_errors)
    r = page.request.get(base_url + "/")
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]


def test_mobile_layout_no_horizontal_scroll(browser, base_url, shot_dir=None):
    ctx = browser.new_context(base_url=base_url, viewport={"width": 400, "height": 800})
    pg = ctx.new_page()
    for path in ("/", "/services", "/products", "/auth/login", "/auth/register"):
        pg.goto(path)
        assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), path
    ctx.close()
