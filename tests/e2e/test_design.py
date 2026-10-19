"""Vérifications du design (Tailwind, logo, polices) dans un vrai navigateur."""
import re
from urllib.parse import urljoin, urlparse

import pytest
from PIL import Image
from playwright.sync_api import expect

from app.auth.security import hash_password
from app.db.base import SessionLocal
from app.db.models.core import Role, User

EMAIL, PASSWORD = "design-admin@test.kyja", "admin password 123"
VIEWPORTS = {"mobile": (360, 740), "tablette": (768, 1024), "bureau": (1440, 900)}


@pytest.fixture
def seeded(page, tmp_path):
    """Crée un admin, une prestation et un produit, chacun avec une vraie image."""
    with SessionLocal() as db:
        db.add(User(email=EMAIL, password_hash=hash_password(PASSWORD), role=Role.admin, full_name="Design"))
        db.commit()
    img = tmp_path / "photo.png"
    Image.new("RGB", (900, 1100), (200, 160, 90)).save(img)

    page.goto("/auth/login")
    page.fill("input[name=email]", EMAIL)
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    for kind, name, extra in (
        ("services", "TEST Soin visage", {"base_duration_min": "60"}),
        ("products", "TEST Huile capillaire", {"stock": "4"}),
    ):
        page.goto(f"/admin/{kind}/new")
        page.fill("input[name=name]", name)
        page.fill("input[name=price]", "34.50")
        for field, value in extra.items():
            page.fill(f"input[name={field}]", value)
        page.click("form.card >> button[type=submit]")
        page.set_input_files("input[name=file]", str(img))
        page.click("text=Téléverser")
        expect(page.locator(".media-list img")).to_have_count(1, timeout=20000)
    page.goto("/auth/logout")  # GET refusé : on se déconnecte via le bouton
    page.goto("/")
    page.click("text=Déconnexion")


def test_fonts_loaded_and_brand_colors(page, seeded):
    page.goto("/")
    page.wait_for_load_state("networkidle")
    assert page.evaluate("document.fonts.check('600 24px \"Cormorant Garamond\"')")
    assert page.evaluate("document.fonts.check('300 16px Jost')")
    fam = page.evaluate("getComputedStyle(document.querySelector('h1')).fontFamily")
    assert "Cormorant" in fam
    assert page.evaluate("getComputedStyle(document.body).fontFamily").startswith("Jost")
    header_bg = page.evaluate("getComputedStyle(document.querySelector('header')).backgroundColor")
    assert header_bg.startswith("oklab") or header_bg.startswith("rgb")  # fond sombre appliqué


@pytest.mark.parametrize("path", ["/", "/services", "/products", "/auth/login", "/auth/register"])
def test_all_images_load_and_have_alt(page, seeded, path):
    page.goto(path)
    page.wait_for_load_state("networkidle")
    broken = page.evaluate(
        "[...document.images].filter(i => i.loading !== 'lazy' && !(i.complete && i.naturalWidth > 0)).map(i => i.src)"
    )
    assert broken == []
    missing_alt = page.evaluate("[...document.images].filter(i => !i.hasAttribute('alt')).map(i => i.src)")
    assert missing_alt == []


def test_catalog_tiles_show_real_photos(page, seeded):
    page.goto("/services")
    img = page.locator(".tile img").first
    img.scroll_into_view_if_needed()
    page.wait_for_load_state("networkidle")
    assert page.evaluate("(el) => el.complete && el.naturalWidth > 0", img.element_handle())
    expect(page.locator(".tile .price").first).to_contain_text("34.50 $ CAD")
    # chiffres alignés (le « 1 » de Cormorant ne doit pas ressembler à un « I »)
    assert page.evaluate("getComputedStyle(document.querySelector('.price')).fontVariantNumeric") == "lining-nums"


def test_internal_links_are_valid(page, seeded, base_url):
    seen, todo = set(), ["/", "/services", "/products", "/auth/login", "/auth/register"]
    while todo:
        path = todo.pop()
        if path in seen:
            continue
        seen.add(path)
        resp = page.goto(path)
        assert resp.status == 200, f"{path} -> {resp.status}"
        for href in page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))"):
            absolute = urljoin(base_url + path, href)
            u = urlparse(absolute)
            if u.netloc == urlparse(base_url).netloc and not u.path.startswith(("/admin", "/auth/logout")):
                todo.append(u.path + (f"?{u.query}" if u.query else ""))
    assert {"/", "/services", "/products", "/auth/login", "/auth/register"} <= seen
    assert any(re.fullmatch(r"/services/\d+", p) for p in seen)  # fiche prestation atteinte par les tuiles
    assert any(re.fullmatch(r"/products/\d+", p) for p in seen)  # fiche produit atteinte par les tuiles


@pytest.mark.parametrize("label", list(VIEWPORTS))
def test_no_overflow_at_each_breakpoint(browser, base_url, seeded, label):
    w, h = VIEWPORTS[label]
    ctx = browser.new_context(base_url=base_url, viewport={"width": w, "height": h})
    pg = ctx.new_page()
    for path in ("/", "/services", "/products", "/services/1", "/auth/login", "/auth/register"):
        pg.goto(path)
        pg.wait_for_load_state("networkidle")
        assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), (label, path)
    nav_visible = pg.locator("nav[aria-label='Navigation principale']").is_visible()
    assert nav_visible == (w >= 768)  # menu replié sous 768 px, déployé au-dessus
    ctx.close()


def test_mobile_menu_toggle_without_js(browser, base_url):
    ctx = browser.new_context(base_url=base_url, viewport={"width": 360, "height": 740}, java_script_enabled=False)
    pg = ctx.new_page()
    pg.goto("/")
    nav = pg.locator("nav[aria-label='Navigation principale']")
    expect(nav).to_be_hidden()
    pg.click("label[for=menu-toggle]")
    expect(nav).to_be_visible()
    ctx.close()


def test_keyboard_focus_is_visible(page):
    page.goto("/auth/login")
    page.keyboard.press("Tab")
    focused = page.evaluate("document.activeElement.tagName")
    assert focused in {"A", "INPUT", "LABEL", "BUTTON"}
    outline = page.evaluate("getComputedStyle(document.activeElement).outlineStyle")
    assert outline != "none"


def test_logo_has_transparent_background(page):
    page.goto("/")
    # le logo du hero doit être détouré : coin supérieur gauche transparent
    alpha = page.evaluate(
        """async () => { const img = document.querySelector('img[alt="KYJA BEAUTÉ"]');
           await img.decode(); const c = document.createElement('canvas');
           c.width = img.naturalWidth; c.height = img.naturalHeight;
           const x = c.getContext('2d'); x.drawImage(img, 0, 0); return x.getImageData(2, 2, 1, 1).data[3]; }"""
    )
    assert alpha == 0
    assert re.search(r"logo-512\.webp$", page.get_attribute("img[alt='KYJA BEAUTÉ']", "src"))
