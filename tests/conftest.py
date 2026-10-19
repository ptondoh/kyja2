"""Configuration des tests.

ISOLATION : les tests n'utilisent JAMAIS la base de développement. Ils tournent sur `kyja_test`
(TEST_DATABASE_URL pour en changer) avec leurs propres dossiers de fichiers. Ainsi les données de
démonstration ou de travail de la base `kyja` ne perturbent pas les tests, et inversement.
Ces variables doivent être posées AVANT tout import de l'application.
"""
import os
import shutil
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DEFAULT_TEST_DB = "postgresql+psycopg://kyja:kyja@localhost:5432/kyja_test"
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", _DEFAULT_TEST_DB)
assert os.environ["DATABASE_URL"].rsplit("/", 1)[-1].endswith("_test"), "les tests doivent viser une base *_test"
_TEST_FILES = _ROOT / "tmp_uploads" / "test_files"
os.environ["MEDIA_DIR"] = str(_TEST_FILES / "media")
os.environ["PRIVATE_DIR"] = str(_TEST_FILES / "private")
os.environ["UPLOAD_TMP_DIR"] = str(_TEST_FILES / "uploads")

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, or_, select

from app.auth.ratelimit import login_limiter, register_limiter
from app.auth.security import hash_password
from app.bookings.photos import anon_ip_limiter, upload_limiter
from app.bookings.router import browse_limiter
from app.bookings.engine import salon_tz
from app.db.base import SessionLocal
from app.db.models.core import (
    Appointment,
    AppointmentResource,
    AvailabilitySlot,
    BookingDraft,
    BookingPhoto,
    ConsentRecord,
    FinancialRecord,
    Hairdresser,
    Media,
    PricingMode,
    Product,
    RefreshToken,
    Resource,
    Role,
    Service,
    ServiceResource,
    User,
)
from app.db.models.settings import AppSetting
from app.main import app
from app.salon import reset_cache as reset_salon_cache
from app.media.service import delete_media_files
from app.media.storage import get_private_storage

TEST_DOMAIN = "@test.kyja"
WALK_IN_DOMAIN = "@sans-courriel.kyja"  # fiches créées par l'admin (réservation par téléphone)


def _test_user_filter():
    return or_(User.email.like(f"%{TEST_DOMAIN}"), User.email.like(f"%{WALK_IN_DOMAIN}"))


def _purge(db) -> None:
    """Supprime toutes les données créées par les tests (comptes @test.kyja, entités « TEST … »)."""
    for owner, col in ((Service, Media.service_id), (Product, Media.product_id)):
        ids = db.scalars(select(owner.id).where(owner.name.like("TEST %"))).all()
        if ids:
            for m in db.scalars(select(Media).where(col.in_(ids))):
                delete_media_files(m)
            db.execute(delete(Media).where(col.in_(ids)))

    uids = db.scalars(select(User.id).where(_test_user_filter())).all()
    hids = (
        db.scalars(select(Hairdresser.id).where(Hairdresser.user_id.in_(uids))).all() if uids else []
    )
    sids = db.scalars(select(Service.id).where(Service.name.like("TEST %"))).all()
    # Photos privées (y compris celles envoyées sans connexion) et brouillons : base de test, on purge tout.
    storage = get_private_storage()
    for ph in db.scalars(select(BookingPhoto)):
        storage.delete(ph.storage_key)
    db.execute(delete(BookingPhoto))
    db.execute(delete(BookingDraft))
    cond = []
    if uids:
        cond.append(Appointment.client_id.in_(uids))
    if hids:
        cond.append(Appointment.hairdresser_id.in_(hids))
    if sids:
        cond.append(Appointment.service_id.in_(sids))
    appt_ids = db.scalars(select(Appointment.id).where(or_(*cond))).all() if cond else []
    if appt_ids:
        db.execute(delete(FinancialRecord).where(FinancialRecord.appointment_id.in_(appt_ids)))
        db.execute(delete(AppointmentResource).where(AppointmentResource.appointment_id.in_(appt_ids)))
        db.execute(delete(Appointment).where(Appointment.id.in_(appt_ids)))
    if hids:
        db.execute(delete(AvailabilitySlot).where(AvailabilitySlot.hairdresser_id.in_(hids)))
        db.execute(delete(Hairdresser).where(Hairdresser.id.in_(hids)))
    if sids:
        db.execute(delete(ServiceResource).where(ServiceResource.service_id.in_(sids)))
        db.execute(delete(Service).where(Service.id.in_(sids)))
    db.execute(delete(Product).where(Product.name.like("TEST %")))
    db.execute(delete(Resource).where(Resource.name.like("TEST %")))
    db.execute(delete(AppSetting))
    db.commit()
    if uids:
        db.execute(delete(RefreshToken).where(RefreshToken.user_id.in_(uids)))
        db.execute(delete(ConsentRecord).where(ConsentRecord.user_id.in_(uids)))
        db.execute(delete(User).where(User.id.in_(uids)))
        db.commit()


@pytest.fixture(scope="session", autouse=True)
def _test_database():
    """Met le schéma de la base de test à jour (migrations Alembic) avant le premier test."""
    from alembic import command
    from alembic.config import Config

    command.upgrade(Config(str(_ROOT / "alembic.ini")), "head")
    yield
    shutil.rmtree(_TEST_FILES, ignore_errors=True)


@pytest.fixture(autouse=True)
def _clean():
    login_limiter.clear()
    register_limiter.clear()
    upload_limiter.clear()
    anon_ip_limiter.clear()
    browse_limiter.clear()
    reset_salon_cache()
    yield
    with SessionLocal() as db:
        _purge(db)


@pytest.fixture
def client():
    c = TestClient(app, follow_redirects=False)
    c.get("/auth/login")  # pose le cookie CSRF
    return c


def csrf(c: TestClient) -> str:
    return c.cookies.get("csrf_token")


def register(c, email="a@test.kyja", password="correct horse battery", **extra):
    data = {
        "full_name": "Test User",
        "email": email,
        "password": password,
        "accept_terms": "true",
        "csrf_token": csrf(c),
        **extra,
    }
    return c.post("/auth/register", data=data)


def login(c, email="a@test.kyja", password="correct horse battery"):
    return c.post("/auth/login", data={"email": email, "password": password, "csrf_token": csrf(c)})


def make_admin(c, email="admin@test.kyja", password="admin password 123"):
    with SessionLocal() as db:
        db.add(User(email=email, password_hash=hash_password(password), role=Role.admin, full_name="Admin Test"))
        db.commit()
    assert login(c, email, password).status_code == 303


# --- Fabriques pour les réservations -----------------------------------------------


def next_weekday(weekday: int = 0, min_days_ahead: int = 3) -> date:
    """Prochain jour de la semaine donné (0 = lundi), au moins `min_days_ahead` jours plus tard."""
    d = datetime.now(salon_tz()).date() + timedelta(days=min_days_ahead)
    while d.weekday() != weekday:
        d += timedelta(days=1)
    return d


def local_start(day: date, hour: int, minute: int = 0) -> datetime:
    """Instant UTC correspondant à une heure locale du salon."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=salon_tz()).astimezone(timezone.utc)


def make_hairdresser(
    db, name="Marie", coefficient="1.00", slots=((0, 9 * 60, 17 * 60),), email=None
) -> Hairdresser:
    user = User(
        email=email or f"{name.lower()}@test.kyja",
        password_hash=hash_password("hairdresser password 1"),
        role=Role.hairdresser,
        full_name=name,
    )
    db.add(user)
    db.flush()
    h = Hairdresser(user_id=user.id, duration_coefficient=Decimal(coefficient))
    db.add(h)
    db.flush()
    for weekday, start, end in slots:
        db.add(AvailabilitySlot(hairdresser_id=h.id, weekday=weekday, start_minute=start, end_minute=end))
    db.commit()
    return h


def make_service(
    db, name="TEST Brushing", base=60, buffer=0, price="55.00", mode=PricingMode.estimate, kinds=()
) -> Service:
    s = Service(
        name=name,
        base_duration_min=base,
        buffer_min=buffer,
        pricing_mode=mode,
        price=Decimal(price) if mode == PricingMode.estimate else None,
    )
    db.add(s)
    db.flush()
    for kind in kinds:
        db.add(ServiceResource(service_id=s.id, resource_kind=kind))
    db.commit()
    return s


def make_client_user(db, email="cliente@test.kyja") -> User:
    u = User(email=email, password_hash=hash_password("client password 12"), role=Role.client, full_name="Cliente")
    db.add(u)
    db.commit()
    return u


def make_resource(db, kind="wash_basin", name="TEST Bac 1") -> Resource:
    r = Resource(kind=kind, name=name)
    db.add(r)
    db.commit()
    return r
