"""Prix final d'une demande sur mesure, agenda de la coiffeuse et carrousel, dans un vrai navigateur."""
import re

from playwright.sync_api import expect
from sqlalchemy import select

from app.bookings import engine
from app.db.base import SessionLocal
from app.db.models.core import Appointment, AppointmentStatus, Hairdresser, MessageOutbox, User
from tests.conftest import local_start, make_media_file, make_service, next_weekday
from tests.e2e.test_booking_flow import CLIENT, salon, sign_in  # noqa: F401  (fixture `salon`)

# Remplace l'alerte du navigateur par un enregistreur (le vrai Notification demande une autorisation humaine).
RECORD_ALERTS = """
window.__alerts = [];
window.Notification = class { constructor(t, o) { window.__alerts.push([t, (o || {}).body]); } };
window.Notification.permission = 'granted';
window.Notification.requestPermission = () => Promise.resolve('granted');
"""
HAIRDRESSER = ("marie@test.kyja", "hairdresser password 1")


def custom_request(hour=10):
    """Demande sur mesure de la cliente chez Marie (créée directement en base : le parcours est testé ailleurs)."""
    with SessionLocal() as db:
        client = db.scalar(select(User).where(User.email == CLIENT[0]))
        marie = db.scalar(select(User).where(User.email == HAIRDRESSER[0]))
        h = db.scalar(select(Hairdresser).where(Hairdresser.user_id == marie.id))
        appt = engine.create_booking(
            db, client=client, service=engine.custom_request_service(db), hairdresser_id=h.id,
            start=local_start(next_weekday(0), hour), notes="Tresses africaines avec rajouts, longueur taille",
        )  # fmt: skip
        return appt.id


def status_of(appt_id):
    with SessionLocal() as db:
        return db.get(Appointment, appt_id).status


# ------------------------------------------------------------------------------------------------ carrousel


def test_photos_and_videos_open_in_a_carousel(page, shot, salon):
    with SessionLocal() as db:
        svc = make_service(db, "TEST Coiffure galerie", price="80.00")
        make_media_file(db, svc, "image", "a")
        make_media_file(db, svc, "image", "b")
        make_media_file(db, svc, "video", "c")
        sid = svc.id
    page.goto(f"/services/{sid}")
    expect(page.locator(".gallery [data-lightbox-item]")).to_have_count(3)
    expect(page.locator("dialog.lightbox")).to_have_count(0)  # rien tant qu'on n'a pas cliqué

    page.locator(".gallery-photo").first.click()
    box = page.locator("dialog.lightbox[open]")
    expect(box).to_be_visible()
    expect(box.locator(".lightbox-count")).to_contain_text("1 / 3")
    expect(box.locator("img.lightbox-media")).to_have_attribute("src", re.compile(r"/media-files/services/\d+/a\.webp"))
    shot("70-carrousel")

    page.keyboard.press("ArrowRight")
    expect(box.locator(".lightbox-count")).to_contain_text("2 / 3")
    page.click(".lightbox-next")
    expect(box.locator(".lightbox-count")).to_contain_text("3 / 3")
    expect(box.locator("video.lightbox-media")).to_have_count(1)  # la vidéo se lit dans la visionneuse
    assert box.locator("video.lightbox-media").get_attribute("poster")
    page.click(".lightbox-next")  # reboucle au début
    expect(box.locator(".lightbox-count")).to_contain_text("1 / 3")
    expect(box.locator("video")).to_have_count(0)  # la vidéo est arrêtée et retirée

    page.keyboard.press("ArrowLeft")
    expect(box.locator(".lightbox-count")).to_contain_text("3 / 3")
    # glissement du doigt vers la gauche = suivante
    page.evaluate(
        """() => { const s = document.querySelector('.lightbox-stage');
           const ev = (t, x) => s.dispatchEvent(new PointerEvent(t, {pointerType: 'touch', clientX: x, bubbles: true}));
           ev('pointerdown', 300); ev('pointerup', 100); }"""
    )
    expect(box.locator(".lightbox-count")).to_contain_text("1 / 3")

    page.keyboard.press("Escape")
    expect(page.locator("dialog.lightbox[open]")).to_have_count(0)
    expect(page.locator(".gallery-photo").first).to_be_focused()  # le focus revient là où on était
    assert page.console_errors == []

    # la petite icône d'une vidéo ouvre aussi la visionneuse, directement sur la vidéo
    page.locator(".gallery-video .expand").click()
    expect(page.locator(".lightbox-count")).to_contain_text("3 / 3")
    page.click(".lightbox-close")
    expect(page.locator("dialog.lightbox[open]")).to_have_count(0)


def test_single_photo_has_no_arrows(page, salon):
    with SessionLocal() as db:
        svc = make_service(db, "TEST Une seule photo", price="80.00")
        make_media_file(db, svc, "image", "seule")
        sid = svc.id
    page.goto(f"/services/{sid}")
    page.locator(".gallery-photo").click()
    expect(page.locator(".lightbox-count")).to_contain_text("1 / 1")
    expect(page.locator(".lightbox-next")).to_be_hidden()
    expect(page.locator(".lightbox-prev")).to_be_hidden()


# ------------------------------------------------------------------------------------- prix et alerte du navigateur


def test_price_flow_alert_in_browser_then_client_accepts(browser, base_url, salon):
    appt_id = custom_request()

    client_ctx = browser.new_context(base_url=base_url)
    client_ctx.add_init_script(RECORD_ALERTS)
    client = client_ctx.new_page()
    client.goto("/auth/login?next=/bookings")
    sign_in(client, CLIENT)
    expect(client.locator("#bell")).to_be_visible()
    expect(client.locator("[data-bell-count]")).to_be_hidden()  # rien de nouveau

    # la coiffeuse (autre navigateur) voit la demande AVANT toute confirmation, et propose le prix
    marie_ctx = browser.new_context(base_url=base_url)
    marie = marie_ctx.new_page()
    marie.goto("/auth/login?next=/agenda")
    sign_in(marie, HAIRDRESSER)
    expect(marie.locator("h1")).to_have_text("Mon agenda")
    group = marie.locator("section[data-group='Prix à proposer']")
    expect(group.locator("a", has_text="Cliente E2E")).to_be_visible()
    group.locator("a", has_text="Cliente E2E").click()
    expect(marie.locator("body")).to_contain_text("Tresses africaines")
    marie.fill("input[name=price]", "140,50")
    marie.click("text=Envoyer à la cliente")
    expect(marie.locator("[role=status]")).to_contain_text("Prix envoyé à la cliente")
    marie.goto("/agenda")
    expect(marie.locator("section[data-group='En attente de la cliente'] a", has_text="Cliente E2E")).to_be_visible()

    # la cliente, site ouvert : la cloche s'allume et l'alerte du navigateur apparaît (sans recharger la page)
    client.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    expect(client.locator("[data-bell-count]")).to_have_text("1", timeout=10000)
    alerts = client.evaluate("window.__alerts")
    assert len(alerts) == 1 and "140,50" in alerts[0][0].replace(".", ",") and "Confirmez ou refusez" in alerts[0][1]

    client.click("#bell")
    expect(client.locator("[data-notification]").first).to_contain_text("Votre prix est prêt")
    client.locator("[data-notification] >> text=Ouvrir").first.click()
    expect(client.locator("body")).to_contain_text("Votre prix est prêt : 140.50 $ CAD")
    client.click("text=J'accepte 140.50 $ CAD")
    expect(client.locator(".status").first).to_have_text("Confirmé")
    assert status_of(appt_id) == AppointmentStatus.confirmed
    # la coiffeuse voit maintenant le rendez-vous confirmé
    marie.goto("/agenda")
    expect(marie.locator("section[data-group='Confirmés à venir'] a", has_text="Cliente E2E")).to_be_visible()
    client_ctx.close()
    marie_ctx.close()


def test_client_declines_from_the_emailed_link_without_logging_in(browser, base_url, page, shot, salon):
    appt_id = custom_request()
    marie_ctx = browser.new_context(base_url=base_url)
    marie = marie_ctx.new_page()
    marie.goto("/auth/login?next=/agenda")
    sign_in(marie, HAIRDRESSER)
    marie.goto(f"/agenda/{appt_id}")
    marie.fill("input[name=price]", "99")
    marie.click("text=Envoyer à la cliente")
    expect(marie.locator("[role=status]")).to_contain_text("Prix envoyé")

    with SessionLocal() as db:
        body = db.scalar(select(MessageOutbox.body).where(MessageOutbox.channel == "email"))
    link = re.search(r"http://localhost:8000(/quotes/[\w-]+)", body).group(1)

    page.goto(link)  # navigateur sans aucune session
    expect(page.locator("h1")).to_have_text("Votre prix est prêt")
    expect(page.locator(".recap")).to_contain_text("99.00 $ CAD")
    shot("71-lien-prix")
    page.click("text=Je refuse")
    expect(page.locator("[role=status]")).to_contain_text("Rendez-vous annulé")
    assert status_of(appt_id) == AppointmentStatus.cancelled
    page.goto(link)  # le lien ne sert qu'une fois
    expect(page.locator("h1")).to_have_text("Lien non valide")
    marie.goto("/agenda")
    expect(marie.locator("section[data-group='Passés et annulés'] a", has_text="Cliente E2E")).to_be_visible()
    marie_ctx.close()


def test_client_chooses_how_to_be_alerted_in_her_profile(page, shot, salon):
    page.goto("/auth/login?next=/clients/me")
    sign_in(page, CLIENT)
    section = page.locator("#notifications")
    expect(section).to_be_visible()
    expect(section.locator("input[name=sms]")).to_be_disabled()  # pas de téléphone : le SMS est impossible
    section.locator("input[name=email]").uncheck()
    section.locator("button[type=submit]").click()
    expect(page.locator("[role=status]")).to_contain_text("alertes sont enregistrés")
    expect(page.locator("#notifications input[name=email]")).not_to_be_checked()
    shot("72-profil-alertes")
