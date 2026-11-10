"""Prix final d'une demande sur mesure : proposition, avertissement de la cliente, confirmation ou refus."""
import re
import threading
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.bookings import engine, quotes
from app.db.base import SessionLocal
from app.db.models.core import Appointment, AppointmentStatus, MessageOutbox, Notification, User
from app.main import app
from app.notifications import providers
from app.notifications import service as notifications
from tests.conftest import csrf, local_start, login, make_admin, make_hairdresser, make_service, next_weekday, register

NOTES = "Tresses africaines avec rajouts, longueur taille"


@pytest.fixture
def setup():
    with SessionLocal() as db:
        marie = make_hairdresser(db, "Marie", "1.00", slots=tuple((d, 540, 1020) for d in range(7)))
        sonia = make_hairdresser(db, "Sonia", "1.00", slots=tuple((d, 540, 1020) for d in range(7)))
        return {
            "marie": marie.id,
            "sonia": sonia.id,
            "catalog": make_service(db, "TEST Brushing", base=60, price="55.00").id,
            "custom": engine.custom_request_service(db).id,
        }


def visitor():
    c = TestClient(app, follow_redirects=False)
    c.get("/auth/login")
    return c


def staff(email, password="hairdresser password 1"):
    c = visitor()
    assert login(c, email, password).status_code == 303
    return c


def book(c, setup, hour=10, key="custom", who="marie", notes=NOTES, day=None):
    day = day or next_weekday(0)
    r = c.post("/bookings", data={"service_id": setup[key], "hairdresser": str(setup[who]), "start": local_start(day, hour).isoformat(),
                                  "notes": notes if key == "custom" else "", "csrf_token": csrf(c)})  # fmt: skip
    assert r.status_code == 303, r.text[:300]
    return int(re.search(r"/bookings/(\d+)", r.headers["location"]).group(1))


def appt(appt_id):
    with SessionLocal() as db:
        a = db.get(Appointment, appt_id)
        db.expunge(a)
        return a


def propose(h_client, appt_id, price="120,50"):
    return h_client.post(f"/agenda/{appt_id}/quote", data={"price": price, "csrf_token": csrf(h_client)})


def last_link():
    """Dernier lien /quotes/<jeton> écrit dans le courriel de l'aperçu."""
    with SessionLocal() as db:
        body = db.scalar(select(MessageOutbox.body).where(MessageOutbox.channel == "email").order_by(MessageOutbox.id.desc()))
    return re.search(r"/quotes/([\w-]+)", body).group(1)


@pytest.fixture
def scene(setup):
    """Une cliente, sa demande sur mesure chez Marie, et Marie connectée."""
    cl = visitor()
    register(cl, email="cliente@test.kyja")
    appt_id = book(cl, setup)
    return {"cl": cl, "appt": appt_id, "m": staff("marie@test.kyja"), **setup}


# --- réservation sur mesure : en attente de prix, créneau gardé ---------------------------------------------------


def test_custom_booking_waits_for_a_price_and_keeps_the_slot(setup, scene):
    assert appt(scene["appt"]).status == AppointmentStatus.pending_quote
    other = visitor()
    register(other, email="autre@test.kyja")
    r = other.post("/bookings", data={"service_id": setup["catalog"], "hairdresser": str(setup["marie"]),
                                      "start": local_start(next_weekday(0), 10).isoformat(), "csrf_token": csrf(other)})  # fmt: skip
    assert r.status_code == 409  # le créneau est pris même sans prix


def test_staff_is_alerted_of_a_new_request(scene):
    with SessionLocal() as db:
        marie = db.scalar(select(User).where(User.email == "marie@test.kyja"))
        titles = db.scalars(select(Notification.title).where(Notification.user_id == marie.id)).all()
    assert any("sur mesure" in t for t in titles)


# --- proposition du prix par la coiffeuse -------------------------------------------------------------------------


def test_hairdresser_proposes_price_and_client_is_notified_with_a_link(scene):
    r = propose(scene["m"], scene["appt"])
    assert r.status_code == 303 and r.headers["location"].endswith("?sent=1")
    a = appt(scene["appt"])
    assert a.status == AppointmentStatus.awaiting_client and a.quoted_price == Decimal("120.50") and a.quote_token_hash
    with SessionLocal() as db:
        client = db.scalar(select(User).where(User.email == "cliente@test.kyja"))
        note = db.scalar(select(Notification).where(Notification.user_id == client.id, Notification.kind == "quote_ready"))
        mail = db.scalar(select(MessageOutbox).where(MessageOutbox.user_id == client.id, MessageOutbox.channel == "email"))
    assert note and "120,50" in note.title.replace(".", ",") or "120.50" in note.title
    assert mail.status == "preview" and mail.provider == "preview"  # aucune clé Resend : rien n'est parti
    assert f"/quotes/" in mail.body and "http://localhost:8000" in mail.body
    # le jeton n'est jamais stocké en clair
    assert last_link() not in (a.quote_token_hash or "")


def test_client_chooses_channels_in_her_profile(scene):
    cl = scene["cl"]
    r = cl.post("/clients/me/notifications", data={"csrf_token": csrf(cl)})  # tout décoché
    assert r.status_code == 303
    propose(scene["m"], scene["appt"])
    with SessionLocal() as db:
        client = db.scalar(select(User).where(User.email == "cliente@test.kyja"))
        assert db.scalars(select(Notification).where(Notification.user_id == client.id, Notification.kind == "quote_ready")).all() == []
        assert db.scalars(select(MessageOutbox).where(MessageOutbox.user_id == client.id)).all() == []


def test_sms_needs_a_valid_phone_and_is_previewed_without_twilio(scene):
    cl = scene["cl"]
    # sans téléphone valide, le SMS ne peut pas être activé
    cl.post("/clients/me/notifications", data={"in_app": "true", "sms": "true", "csrf_token": csrf(cl)})
    with SessionLocal() as db:
        assert db.scalar(select(User.pref_sms).where(User.email == "cliente@test.kyja")) is False
    cl.post("/clients/me", data={"full_name": "Cliente", "phone": "514 555-0111", "address": "", "csrf_token": csrf(cl)})
    cl.post("/clients/me/notifications", data={"in_app": "true", "sms": "true", "csrf_token": csrf(cl)})
    propose(scene["m"], scene["appt"])
    with SessionLocal() as db:
        sms = db.scalar(select(MessageOutbox).where(MessageOutbox.channel == "sms"))
    assert sms and sms.status == "preview" and sms.to_address == "514 555-0111" and "/quotes/" in sms.body and len(sms.body) < 320


@pytest.mark.parametrize("bad", ["", "abc", "-5", "0", "100000", "12,345", "nan", "inf"])
def test_invalid_prices_are_refused(scene, bad):
    r = propose(scene["m"], scene["appt"], bad)
    assert r.status_code in (400, 422)
    assert appt(scene["appt"]).status == AppointmentStatus.pending_quote


def test_catalog_booking_has_no_price_to_propose(setup):
    cl = visitor()
    register(cl, email="cliente@test.kyja")
    catalog_appt = book(cl, setup, key="catalog")
    m = staff("marie@test.kyja")
    assert propose(m, catalog_appt).status_code == 400
    assert appt(catalog_appt).status == AppointmentStatus.confirmed


# --- réponse de la cliente par le lien -----------------------------------------------------------------------------


def test_link_page_shows_price_and_accept_confirms(scene):
    propose(scene["m"], scene["appt"])
    token = last_link()
    anon = visitor()  # pas de connexion nécessaire : le jeton suffit
    page = anon.get(f"/quotes/{token}")
    assert page.status_code == 200 and "120.50" in page.text and "J'accepte" in page.text
    assert page.headers["cache-control"] == "no-store" and page.headers["referrer-policy"] == "no-referrer"
    done = anon.post(f"/quotes/{token}", data={"decision": "accept", "csrf_token": csrf(anon)})
    assert done.status_code == 200 and "Rendez-vous confirmé" in done.text
    a = appt(scene["appt"])
    assert a.status == AppointmentStatus.confirmed and a.quote_token_hash is None and a.quote_decided_at
    assert anon.get(f"/quotes/{token}").status_code == 404  # lien à usage unique
    with SessionLocal() as db:  # l'équipe est prévenue
        assert db.scalar(select(Notification).where(Notification.kind == "quote_accepted"))


def test_decline_cancels_and_frees_the_slot(setup, scene):
    propose(scene["m"], scene["appt"])
    token = last_link()
    anon = visitor()
    done = anon.post(f"/quotes/{token}", data={"decision": "decline", "csrf_token": csrf(anon)})
    assert done.status_code == 200 and "Rendez-vous annulé" in done.text
    a = appt(scene["appt"])
    assert a.status == AppointmentStatus.cancelled and a.cancel_reason == "quote_declined"
    other = visitor()
    register(other, email="autre@test.kyja")
    assert book(other, setup, key="catalog") > 0  # le créneau est libre à nouveau
    with SessionLocal() as db:
        assert db.scalar(select(Notification).where(Notification.kind == "quote_declined"))


def test_unknown_token_is_a_neutral_404_and_guessing_is_throttled(setup):
    anon = visitor()
    for _ in range(20):
        assert anon.get("/quotes/" + "x" * 43).status_code == 404
    assert anon.get("/quotes/" + "x" * 43).status_code == 429


def test_bad_decision_value_is_refused(scene):
    propose(scene["m"], scene["appt"])
    anon = visitor()
    r = anon.post(f"/quotes/{last_link()}", data={"decision": "maybe", "csrf_token": csrf(anon)})
    assert r.status_code == 404 and appt(scene["appt"]).status == AppointmentStatus.awaiting_client


def test_post_without_csrf_is_refused(scene):
    propose(scene["m"], scene["appt"])
    anon = visitor()
    assert anon.post(f"/quotes/{last_link()}", data={"decision": "accept"}).status_code == 403


def test_a_new_price_invalidates_the_previous_link(scene):
    propose(scene["m"], scene["appt"], "100")
    old = last_link()
    propose(scene["m"], scene["appt"], "150")
    new = last_link()
    assert old != new
    anon = visitor()
    assert anon.get(f"/quotes/{old}").status_code == 404
    assert "150.00" in anon.get(f"/quotes/{new}").text


def test_expired_proposal_cannot_be_accepted_and_is_cancelled_by_the_sweep(scene):
    propose(scene["m"], scene["appt"])
    token = last_link()
    with SessionLocal() as db:
        a = db.get(Appointment, scene["appt"])
        a.quote_expires_at = engine.utcnow() - timedelta(minutes=1)
        db.commit()
    anon = visitor()
    page = anon.get(f"/quotes/{token}")
    assert "n'est plus valable" in page.text and "J'accepte" not in page.text
    r = anon.post(f"/quotes/{token}", data={"decision": "accept", "csrf_token": csrf(anon)})
    assert r.status_code == 409 and appt(scene["appt"]).status == AppointmentStatus.awaiting_client
    with SessionLocal() as db:
        assert len(quotes.expire_stale_quotes(db)) == 1
    a = appt(scene["appt"])
    assert a.status == AppointmentStatus.cancelled and a.cancel_reason == "quote_expired" and a.quote_token_hash is None


def test_two_simultaneous_answers_only_one_wins(scene):
    propose(scene["m"], scene["appt"])
    results = []

    def answer(accept):
        with SessionLocal() as db:
            try:
                quotes.decide(db, scene["appt"], accept)
                results.append("ok")
            except quotes.QuoteError:
                results.append("refus")

    threads = [threading.Thread(target=answer, args=(flag,)) for flag in (True, False)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["ok", "refus"]


# --- réponse depuis le compte --------------------------------------------------------------------------------------


def test_logged_in_client_can_answer_from_her_booking_page(scene):
    propose(scene["m"], scene["appt"])
    cl = scene["cl"]
    page = cl.get(f"/bookings/{scene['appt']}")
    assert "Votre prix est prêt" in page.text and "J'accepte" in page.text
    r = cl.post(f"/bookings/{scene['appt']}/quote", data={"decision": "accept", "csrf_token": csrf(cl)})
    assert r.status_code == 303 and appt(scene["appt"]).status == AppointmentStatus.confirmed


def test_someone_else_cannot_answer_for_the_client(scene):
    propose(scene["m"], scene["appt"])
    other = visitor()
    register(other, email="autre@test.kyja")
    assert other.post(f"/bookings/{scene['appt']}/quote", data={"decision": "accept", "csrf_token": csrf(other)}).status_code == 404
    assert appt(scene["appt"]).status == AppointmentStatus.awaiting_client


def test_client_can_still_cancel_while_waiting_for_the_price(scene):
    cl = scene["cl"]
    r = cl.post(f"/bookings/{scene['appt']}/cancel", data={"csrf_token": csrf(cl)})
    assert r.status_code == 303
    a = appt(scene["appt"])
    assert a.status == AppointmentStatus.cancelled and a.cancel_reason == "client"
    assert propose(scene["m"], scene["appt"]).status_code == 400  # plus de prix à proposer


# --- agenda de la coiffeuse ----------------------------------------------------------------------------------------


def test_hairdresser_agenda_lists_unconfirmed_requests_by_group_and_type(setup, scene):
    cl2 = visitor()
    register(cl2, email="b@test.kyja")
    catalog_id = book(cl2, setup, key="catalog", hour=14)
    m = scene["m"]
    page = m.get("/agenda")
    assert page.status_code == 200
    assert f"/agenda/{scene['appt']}" in page.text and f"/agenda/{catalog_id}" in page.text
    custom_only = m.get("/agenda?type=custom").text
    assert f"/agenda/{scene['appt']}" in custom_only and f"/agenda/{catalog_id}" not in custom_only
    catalog_only = m.get("/agenda?type=catalog").text
    assert f"/agenda/{catalog_id}" in catalog_only and f"/agenda/{scene['appt']}" not in catalog_only
    detail = m.get(f"/agenda/{scene['appt']}")
    assert "Tresses africaines" in detail.text and "Envoyer à la cliente" in detail.text


def test_hairdresser_sees_only_her_own_clients_and_clients_cannot_open_the_agenda(setup, scene):
    sonia = staff("sonia@test.kyja")
    assert sonia.get(f"/agenda/{scene['appt']}").status_code == 404
    assert propose(sonia, scene["appt"]).status_code == 404
    assert f"/agenda/{scene['appt']}" not in sonia.get("/agenda").text
    assert scene["cl"].get("/agenda").status_code == 403
    assert visitor().get("/agenda").status_code == 401


def test_hairdresser_keeps_her_client_view_for_her_own_bookings(setup, scene):
    m = scene["m"]
    own = book(m, setup, key="catalog", who="sonia", hour=11)
    assert f"/bookings/{own}" in m.get("/bookings").text  # ses propres réservations
    assert f"/bookings/{scene['appt']}" not in m.get("/bookings").text  # pas celles de ses clientes


# --- admin ---------------------------------------------------------------------------------------------------------


def test_admin_proposes_the_price_through_the_final_price_form(scene):
    admin = visitor()
    make_admin(admin)
    r = admin.post(f"/admin/appointments/{scene['appt']}/final-price", data={"price": "99", "csrf_token": csrf(admin)})
    assert r.status_code == 303 and "quoted=1" in r.headers["location"]
    a = appt(scene["appt"])
    assert a.status == AppointmentStatus.awaiting_client and a.final_price is None and a.quoted_price == Decimal("99")
    page = admin.get("/admin/appointments")
    assert "En attente de la cliente" in page.text and "Renvoyer le prix" in page.text


def test_admin_final_price_on_a_catalog_booking_still_completes_it(setup):
    cl = visitor()
    register(cl, email="cliente@test.kyja")
    catalog_id = book(cl, setup, key="catalog")
    admin = visitor()
    make_admin(admin)
    assert admin.post(f"/admin/appointments/{catalog_id}/final-price", data={"price": "60", "csrf_token": csrf(admin)}).status_code == 303
    a = appt(catalog_id)
    assert a.status == AppointmentStatus.completed and a.final_price == Decimal("60.00")


def test_final_price_cannot_be_set_before_the_client_accepts(scene):
    propose(scene["m"], scene["appt"])
    with SessionLocal() as db:
        with pytest.raises(engine.BookingError):
            engine.set_final_price(db, db.get(Appointment, scene["appt"]), Decimal("10"))


def test_admin_message_log_shows_previews(scene):
    propose(scene["m"], scene["appt"])
    admin = visitor()
    make_admin(admin)
    page = admin.get("/admin/messages")
    assert page.status_code == 200 and "aperçu" in page.text and "cliente@test.kyja" in page.text


# --- alertes du site -----------------------------------------------------------------------------------------------


def test_poll_returns_only_my_unread_alerts_and_mark_read_clears_them(scene):
    propose(scene["m"], scene["appt"])
    cl = scene["cl"]
    data = cl.get("/notifications/poll?since=0").json()
    assert data["unread"] == 1 and data["items"][0]["url"] == f"/bookings/{scene['appt']}"
    assert cl.get(f"/notifications/poll?since={data['items'][0]['id']}").json()["items"] == []
    assert visitor().get("/notifications/poll").json()["unread"] == 0  # anonyme : rien
    assert "120" in cl.get("/notifications").text
    cl.post("/notifications/read", data={"csrf_token": csrf(cl)})
    assert cl.get("/notifications/poll?since=0").json()["unread"] == 0
    # les alertes d'une autre personne ne fuient pas
    other = visitor()
    register(other, email="autre@test.kyja")
    assert other.get("/notifications/poll?since=0").json()["items"] == []


# --- fournisseurs (sans réseau) ------------------------------------------------------------------------------------


def test_phone_normalisation():
    assert providers.normalize_phone("514 555-0111") == "+15145550111"
    assert providers.normalize_phone("(514) 555-0111") == "+15145550111"
    assert providers.normalize_phone("1-514-555-0111") == "+15145550111"
    assert providers.normalize_phone("+33 6 12 34 56 78") == "+33612345678"
    assert providers.normalize_phone("123") is None and providers.normalize_phone("") is None


def test_email_html_is_escaped_and_links_are_clickable():
    out = providers.text_to_html("Bonjour <script>alert(1)</script>\nhttps://x.ca/quotes/abc")
    assert "<script>" not in out and "&lt;script&gt;" in out and '<a href="https://x.ca/quotes/abc">' in out


def test_twilio_call_is_well_formed_and_errors_are_reported(monkeypatch):
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "ACtest")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "secret")
    monkeypatch.setenv("TWILIO_FROM", "+15145550000")
    from app.config import get_settings

    get_settings.cache_clear()
    sent = {}

    class Resp:
        def __init__(self, code, body):
            self.status_code, self._body = code, body

        def json(self):
            return self._body

    def fake_post(url, data, auth, timeout):
        sent.update(url=url, data=data, auth=auth)
        return Resp(201, {"sid": "SM123"})

    try:
        monkeypatch.setattr(providers.httpx, "post", fake_post)
        result = providers.send_sms("514 555-0111", "Bonjour")
        assert result.status == "sent" and result.ref == "SM123"
        assert sent["data"] == {"To": "+15145550111", "From": "+15145550000", "Body": "Bonjour"}
        assert sent["url"].endswith("/Accounts/ACtest/Messages.json") and sent["auth"] == ("ACtest", "secret")
        monkeypatch.setattr(providers.httpx, "post", lambda *a, **k: Resp(400, {"message": "bad number"}))
        with pytest.raises(providers.DeliveryError, match="bad number"):
            providers.send_sms("514 555-0111", "x")
        with pytest.raises(providers.DeliveryError):
            providers.send_sms("12", "x")  # numéro inutilisable
    finally:
        get_settings.cache_clear()


def test_a_failed_delivery_is_kept_and_can_be_retried(scene, monkeypatch):
    def boom(*a, **k):
        raise providers.DeliveryError("Resend : 500")

    monkeypatch.setattr(providers, "send_email", boom)
    propose(scene["m"], scene["appt"])
    with SessionLocal() as db:
        mail = db.scalar(select(MessageOutbox).where(MessageOutbox.channel == "email", MessageOutbox.to_address == "cliente@test.kyja"))
        assert mail.status == "failed" and "500" in mail.error and mail.attempts == 1
        mail_id = mail.id
    monkeypatch.undo()
    admin = visitor()
    make_admin(admin)
    assert admin.post(f"/admin/messages/{mail_id}/retry", data={"csrf_token": csrf(admin)}).status_code == 303
    with SessionLocal() as db:
        assert db.get(MessageOutbox, mail_id).status == "preview"


def test_anonymised_or_walk_in_clients_never_get_emails(scene):
    with SessionLocal() as db:
        client = db.scalar(select(User).where(User.email == "cliente@test.kyja"))
        client.email = "w1@sans-courriel.kyja"
        db.commit()
        assert notifications.has_real_email(client) is False
    propose(scene["m"], scene["appt"])
    with SessionLocal() as db:
        assert db.scalars(select(MessageOutbox).where(MessageOutbox.channel == "email", MessageOutbox.user_id == client.id)).all() == []


def test_sweeper_cancels_unanswered_quotes_and_tells_everyone(scene):
    from app.notifications import sweeper

    propose(scene["m"], scene["appt"])
    assert sweeper.sweep_once() == 0  # pas encore expiré
    with SessionLocal() as db:
        db.get(Appointment, scene["appt"]).quote_expires_at = engine.utcnow() - timedelta(minutes=1)
        db.commit()
    assert sweeper.sweep_once() == 1 and sweeper.sweep_once() == 0
    assert appt(scene["appt"]).status == AppointmentStatus.cancelled
    with SessionLocal() as db:
        kinds = db.scalars(select(Notification.kind).where(Notification.kind == "quote_expired")).all()
    assert len(kinds) >= 2  # la cliente et l'équipe
