"""Liste des utilisateurs, promotions (clientes consentantes seulement), désabonnement et statuts actif / désactivé."""
import re
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.auth.security import hash_password
from app.db.base import SessionLocal
from app.db.models.core import Category, ConsentRecord, MarketingCampaign, MessageOutbox, Product, Resource, Role, Service, User
from app.db.models.settings import set_setting
from app.marketing import service as marketing
from tests.conftest import csrf, login, make_admin, make_category, make_resource, make_service
from tests.test_custom_quotes import visitor


def make_user(db, email, name="Cliente Test", *, consent=None, sms=False, phone=None, active=True, role=Role.client, anonymized=False):
    """`consent` : None = jamais répondu ; True / False = dernier consentement ; « [True, False] » = accepté puis retiré."""
    u = User(email=email, password_hash=hash_password("client password 12"), role=role, full_name=name, phone=phone, is_active=active,
             pref_sms=sms, anonymized_at=datetime.now(timezone.utc) if anonymized else None)  # fmt: skip
    db.add(u)
    db.flush()
    for granted in [] if consent is None else (consent if isinstance(consent, list) else [consent]):
        db.add(ConsentRecord(user_id=u.id, kind="marketing", granted=granted))
    db.commit()
    return u.id


@pytest.fixture
def admin():
    c = visitor()
    make_admin(c)
    return c


@pytest.fixture
def people():
    with SessionLocal() as db:
        return {
            "yes": make_user(db, "oui@test.kyja", "Alice Oui", consent=True, sms=True, phone="514 555-0101"),
            "no": make_user(db, "non@test.kyja", "Bob Non", consent=False),
            "never": make_user(db, "jamais@test.kyja", "Chloé Jamais"),
            "withdrew": make_user(db, "retire@test.kyja", "Dina Retire", consent=[True, False]),
            "off": make_user(db, "inactive@test.kyja", "Eve Inactive", consent=True, active=False),
            "walkin": make_user(db, "w1@sans-courriel.kyja", "Fanny Salon", consent=True),
            "gone": make_user(db, "gone@supprime.kyja", "Compte supprimé", consent=True, anonymized=True, active=False),
            "sms_off": make_user(db, "sms-off@test.kyja", "Gina Sansms", consent=True, sms=False, phone="514 555-0102"),
        }


def campaign_form(**over):
    data = {"subject": "-15 % sur les tresses", "body": "Profitez de -15 % sur toutes les tresses ce mois-ci.", "sms_text": "-15 % sur les tresses.", "send_email": "true"}
    data.update(over)
    return data


def create_campaign(admin, **over):
    r = admin.post("/admin/marketing/review", data={**campaign_form(**over), "csrf_token": csrf(admin)})
    assert r.status_code == 303, r.text[:400]
    return int(r.headers["location"].rsplit("/", 1)[1])


def send(admin, cid):
    return admin.post(f"/admin/marketing/{cid}/send", data={"csrf_token": csrf(admin)})


def set_address(value="123, rue Exemple, Montréal (Québec) H0H 0H0"):
    with SessionLocal() as db:
        set_setting(db, "salon_address", value)


def rows(cid):
    with SessionLocal() as db:
        return db.scalars(select(MessageOutbox).where(MessageOutbox.campaign_id == cid).order_by(MessageOutbox.id)).all()


# --- liste des utilisateurs -----------------------------------------------------------------------------------------


def test_users_page_is_admin_only(people):
    anon = visitor()
    assert anon.get("/admin/users").status_code in (303, 401, 403)
    client = visitor()
    login(client, "oui@test.kyja", "client password 12")
    assert client.get("/admin/users").status_code in (303, 403)


def test_users_list_shows_contacts_and_filters(admin, people):
    html = admin.get("/admin/users").text
    assert "oui@test.kyja" in html and "514 555-0101" in html and "Alice Oui" in html
    assert "gone@supprime.kyja" not in html and "Supprimé" in html  # compte supprimé : plus de coordonnées affichées

    def names(**params):
        page = admin.get("/admin/users", params=params).text
        return {n for n in ("Alice Oui", "Bob Non", "Chloé Jamais", "Dina Retire", "Eve Inactive") if n in page}

    assert names(promo="accepte") == {"Alice Oui", "Eve Inactive"}  # « accepte » seul : pas « n'accepte pas »
    assert names(promo="n'accepte") == {"Bob Non", "Chloé Jamais", "Dina Retire"}
    assert names(statut="désactivé") == {"Eve Inactive"}
    assert "Alice Oui" in names(statut="actif") and "Eve Inactive" not in names(statut="actif")
    assert names(q="555-0101") == {"Alice Oui"}
    assert names(q="bob") == {"Bob Non"}
    assert names(role="coiffeuse") == set()


# --- promotions ----------------------------------------------------------------------------------------------------------


def test_only_clients_who_accepted_receive_a_promotion(admin, people):
    set_address()
    cid = create_campaign(admin)
    r = send(admin, cid)
    assert r.status_code == 303
    sent = rows(cid)
    assert [m.to_address for m in sent] == ["oui@test.kyja", "sms-off@test.kyja"]  # ni refus, ni jamais répondu, ni retrait, ni inactive, ni salon
    assert all(m.channel == "email" for m in sent)
    body = sent[0].body
    assert "Bonjour Alice," in body and "-15 %" in body and "123, rue Exemple" in body and "/unsubscribe/" in body
    assert sent[0].subject == "-15 % sur les tresses"
    with SessionLocal() as db:
        c = db.get(MarketingCampaign, cid)
        assert c.status == "sent" and c.recipient_count == 2


def test_sms_goes_only_to_those_who_chose_sms_and_have_a_valid_number(admin, people):
    set_address()
    cid = create_campaign(admin, send_email="", send_sms="true")
    send(admin, cid)
    sent = rows(cid)
    assert [(m.channel, m.to_address) for m in sent] == [("sms", "514 555-0101")]
    assert "Se désabonner" in sent[0].body and "-15 % sur les tresses." in sent[0].body


def test_cannot_send_without_the_salon_address_and_can_without_a_channel(admin, people):
    cid = create_campaign(admin)
    r = send(admin, cid)
    assert r.status_code == 400 and "adresse postale" in r.text
    assert rows(cid) == []
    with SessionLocal() as db:
        assert db.get(MarketingCampaign, cid).status == "draft"
    bad = admin.post("/admin/marketing/review", data={**campaign_form(send_email=""), "csrf_token": csrf(admin)})
    assert bad.status_code == 400 and "au moins un canal" in bad.text
    short = admin.post("/admin/marketing/review", data={**campaign_form(body="court"), "csrf_token": csrf(admin)})
    assert short.status_code == 400


def test_a_promotion_cannot_be_sent_twice(admin, people):
    set_address()
    cid = create_campaign(admin)
    send(admin, cid)
    send(admin, cid)  # double clic ou deuxième onglet
    assert len(rows(cid)) == 2


def test_test_message_goes_to_the_admin_only(admin, people):
    set_address()
    cid = create_campaign(admin)
    r = admin.post(f"/admin/marketing/{cid}/test", data={"csrf_token": csrf(admin)})
    assert r.status_code == 303
    with SessionLocal() as db:
        mails = db.scalars(select(MessageOutbox).where(MessageOutbox.subject.like("[ESSAI]%"))).all()
        assert [m.to_address for m in mails] == ["admin@test.kyja"] and mails[0].campaign_id is None
        assert db.get(MarketingCampaign, cid).status == "draft"


def test_admin_pages_for_promotions(admin, people):
    set_address()
    assert "Promotions" in admin.get("/admin/marketing").text
    assert "Nouvelle promotion" in admin.get("/admin/marketing/new").text
    cid = create_campaign(admin)
    page = admin.get(f"/admin/marketing/{cid}").text
    assert "Envoyer à 3 personnes" in page  # Alice, Gina et Fanny (sans courriel réel) ont accepté ; Eve est inactive
    send(admin, cid)
    done = admin.get(f"/admin/marketing/{cid}").text
    assert "Envoyée" in done and "2 personnes" in done
    assert "promotion" in admin.get("/admin/messages").text
    assert "Envoyée" in admin.get("/admin/marketing", params={"statut": "envoy"}).text
    assert "Aucune promotion" in admin.get("/admin/marketing", params={"statut": "brouillon"}).text
    assert visitor().get("/admin/marketing").status_code in (303, 401, 403)


# --- désabonnement ---------------------------------------------------------------------------------------------------


def test_unsubscribe_link_stops_promotions_without_login(admin, people):
    set_address()
    cid = create_campaign(admin)
    send(admin, cid)
    link = re.search(r"/unsubscribe/[\w.\-]+", rows(cid)[0].body).group(0)
    anon = visitor()
    page = anon.get(link)
    assert page.status_code == 200 and "Confirmer le désabonnement" in page.text
    with SessionLocal() as db:
        assert marketing.audience(db)["consenting"] == 3  # une simple visite (ou un robot de courriel) ne désabonne personne
    done = anon.post(link, data={"csrf_token": csrf(anon)})
    assert done.status_code == 200 and "C'est fait" in done.text
    with SessionLocal() as db:
        assert marketing.audience(db)["consenting"] == 2
        history = db.scalars(select(ConsentRecord.granted).where(ConsentRecord.user_id == people["yes"], ConsentRecord.kind == "marketing").order_by(ConsentRecord.id)).all()
    assert history == [True, False]  # l'historique est conservé
    second = create_campaign(admin)
    send(admin, second)
    assert "oui@test.kyja" not in [m.to_address for m in rows(second)]


def test_forged_or_foreign_unsubscribe_links_do_nothing(people):
    anon = visitor()
    for bad in ("1.abc", f"{people['yes']}.{'0' * 32}", "abc", f"999999999.{'a' * 32}", "x" * 300):
        assert anon.get(f"/unsubscribe/{bad}").status_code == 404
        assert anon.post(f"/unsubscribe/{bad}", data={"csrf_token": csrf(anon)}).status_code == 404
    forged = marketing.unsubscribe_token(people["yes"]).replace(str(people["yes"]), str(people["no"]), 1)
    assert anon.post(f"/unsubscribe/{forged}", data={"csrf_token": csrf(anon)}).status_code == 404  # le jeton d'une autre personne ne marche pas
    assert anon.post(f"/unsubscribe/{marketing.unsubscribe_token(people['yes'])}").status_code in (400, 403)  # sans jeton CSRF


# --- actif / désactivé ------------------------------------------------------------------------------------------------


def test_status_filters_and_toggles(admin):
    with SessionLocal() as db:
        cat_on, cat_off = make_category(db, "TEST Active"), make_category(db, "TEST Eteinte")
        cat_off.is_active = False
        svc = make_service(db, "TEST Prestation x", category=cat_on)
        prod = Product(name="TEST Produit x", price=10, stock=9)
        res_on, res_off = make_resource(db, "wash_basin", "TEST Bac actif"), make_resource(db, "dryer_hood", "TEST Casque éteint")
        res_off.is_active = False
        db.add(prod)
        db.commit()
        ids = {"svc": svc.id, "prod": prod.id, "cat": cat_on.id, "res": res_on.id}

    assert "TEST Eteinte" in admin.get("/admin/categories", params={"statut": "désactivée"}).text
    assert "TEST Eteinte" not in admin.get("/admin/categories", params={"statut": "active"}).text  # « active » ne trouve pas « désactivée »
    assert "TEST Casque éteint" in admin.get("/admin/resources", params={"statut": "désactivé"}).text
    assert "TEST Casque éteint" not in admin.get("/admin/resources", params={"statut": "actif"}).text
    assert "TEST Bac actif" in admin.get("/admin/resources", params={"q": "bac", "kind": "wash"}).text

    for path, key, model in (("services", "svc", Service), ("products", "prod", Product), ("categories", "cat", Category), ("resources", "res", Resource)):
        r = admin.post(f"/admin/{path}/{ids[key]}/toggle", data={"csrf_token": csrf(admin), "next": f"/admin/{path}?statut=actif"})
        assert r.status_code == 303 and r.headers["location"] == f"/admin/{path}?statut=actif", path
        with SessionLocal() as db:
            assert db.get(model, ids[key]).is_active is False, path
        admin.post(f"/admin/{path}/{ids[key]}/toggle", data={"csrf_token": csrf(admin)})
        with SessionLocal() as db:
            assert db.get(model, ids[key]).is_active is True, path
    outside = admin.post(f"/admin/products/{ids['prod']}/toggle", data={"csrf_token": csrf(admin), "next": "https://evil.example/"})
    assert outside.headers["location"] == "/admin/products"  # pas de redirection vers un autre site
    # ce dernier clic l'a désactivé (3e bascule) : il apparaît dans « désactivé » et plus dans « actif »
    assert "TEST Produit x" in admin.get("/admin/products", params={"statut": "désactivé"}).text
    assert "TEST Produit x" not in admin.get("/admin/products", params={"statut": "actif"}).text
    assert "TEST Prestation x" in admin.get("/admin/services", params={"statut": "active"}).text


def test_deactivated_category_is_not_offered_as_a_site_filter():
    with SessionLocal() as db:
        cat = make_category(db, "TEST Cachée")
        make_service(db, "TEST Dans cachée", category=cat)
    anon = visitor()

    def chips(html):
        found = re.search(r'<nav class="chips.*?</nav>', html, re.S)
        return found.group(0) if found else ""  # plus aucune catégorie active : pas de filtres du tout

    assert "TEST Cachée" in chips(anon.get("/services").text)
    with SessionLocal() as db:
        db.get(Category, cat.id).is_active = False
        db.commit()
    page = anon.get("/services").text
    assert "TEST Cachée" not in chips(page) and "TEST Dans cachée" in page  # ses prestations restent visibles
