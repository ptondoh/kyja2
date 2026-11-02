"""La liste des rendez-vous de l'admin doit rester complète quand une cliente annule ou supprime son compte."""
import re

import pytest
from fastapi.testclient import TestClient

from app.bookings import engine
from app.db.base import SessionLocal
from app.main import app
from tests.conftest import csrf, local_start, make_admin, make_hairdresser, make_service, next_weekday, register


@pytest.fixture
def setup():
    with SessionLocal() as db:
        h = make_hairdresser(db, "Marie", "1.00", slots=tuple((d, 540, 1020) for d in range(7)))
        return {"h": h.id, "catalog": make_service(db, "TEST Brushing", base=60, price="55.00").id,
                "custom": engine.custom_request_service(db).id}  # fmt: skip


def visitor():
    c = TestClient(app, follow_redirects=False)
    c.get("/auth/login")
    return c


def book(c, setup, hour, key="catalog", notes=""):
    day = next_weekday(0)
    r = c.post("/bookings", data={"service_id": setup[key], "hairdresser": str(setup["h"]), "start": local_start(day, hour).isoformat(),
                                  "notes": notes, "csrf_token": csrf(c)})  # fmt: skip
    assert r.status_code == 303, r.text[:200]
    return int(re.search(r"/bookings/(\d+)", r.headers["location"]).group(1))


def rows(page: str) -> list[str]:
    """Identifiants des lignes de rendez-vous affichées dans le tableau de l'admin."""
    return re.findall(r'/admin/appointments/(\d+)/final-price', page)


def test_admin_still_sees_every_appointment_after_a_client_cancels(setup):
    a, b = visitor(), visitor()
    register(a, email="a@test.kyja")
    register(b, email="b@test.kyja")
    appt_a, appt_b = book(a, setup, 9), book(b, setup, 11, key="custom", notes="Tresses africaines avec rajouts, longueur taille")
    admin = visitor()
    make_admin(admin)
    assert len(admin.get("/admin/appointments").text.split("<tr>")) >= 3  # en-tête + 2 lignes

    assert a.post(f"/bookings/{appt_a}/cancel", data={"csrf_token": csrf(a)}).status_code == 303
    page = admin.get("/admin/appointments")
    assert page.status_code == 200
    assert str(appt_b) in rows(page.text)  # l'autre rendez-vous (sur mesure, prix à proposer) est toujours là
    assert "Annulé" in page.text and "Prix à proposer" in page.text  # l'annulé reste visible, avec son statut
    assert page.text.count("final-price") >= 1


def test_admin_list_survives_a_client_deleting_the_account(setup):
    a, b = visitor(), visitor()
    register(a, email="a@test.kyja")
    register(b, email="b@test.kyja")
    appt_a, appt_b = book(a, setup, 9), book(b, setup, 11)
    admin = visitor()
    make_admin(admin)
    a.post("/clients/me/delete", data={"password": "correct horse battery", "confirmation": "SUPPRIMER", "csrf_token": csrf(a)})
    page = admin.get("/admin/appointments")
    assert page.status_code == 200 and str(appt_b) in rows(page.text)
    assert "CLIENT_SUPPRIMÉ" in page.text
