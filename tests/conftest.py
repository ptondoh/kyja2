import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.auth.ratelimit import login_limiter, register_limiter
from app.db.base import SessionLocal
from app.db.models.core import ConsentRecord, RefreshToken, User
from app.main import app

TEST_DOMAIN = "@test.kyja"


@pytest.fixture(autouse=True)
def _clean():
    login_limiter.clear()
    register_limiter.clear()
    yield
    with SessionLocal() as db:
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
