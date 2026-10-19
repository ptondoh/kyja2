import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.auth.ratelimit import login_limiter, register_limiter
from app.db.base import SessionLocal
from app.auth.security import hash_password
from app.db.models.core import ConsentRecord, Media, Product, RefreshToken, Role, Service, User
from app.db.models.settings import AppSetting
from app.media.service import delete_media_files
from app.main import app

TEST_DOMAIN = "@test.kyja"


@pytest.fixture(autouse=True)
def _clean():
    login_limiter.clear()
    register_limiter.clear()
    yield
    with SessionLocal() as db:
        for owner, col in ((Service, Media.service_id), (Product, Media.product_id)):
            ids = db.scalars(select(owner.id).where(owner.name.like("TEST %"))).all()
            if ids:
                for m in db.scalars(select(Media).where(col.in_(ids))):
                    delete_media_files(m)
                db.execute(delete(Media).where(col.in_(ids)))
                db.execute(delete(owner).where(owner.id.in_(ids)))
        db.execute(delete(AppSetting))
        db.commit()
        ids = db.scalars(select(User.id).where(User.email.like(f"%{TEST_DOMAIN}"))).all()
        if ids:
            db.execute(delete(RefreshToken).where(RefreshToken.user_id.in_(ids)))
            db.execute(delete(ConsentRecord).where(ConsentRecord.user_id.in_(ids)))
            db.execute(delete(User).where(User.id.in_(ids)))
            db.commit()


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
