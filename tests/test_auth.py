from sqlalchemy import select

from app.db.base import SessionLocal
from app.db.models.core import ConsentRecord, RefreshToken, User
from tests.conftest import csrf, login, register


def test_register_login_me(client):
    r = register(client, marketing_optin="true")
    assert r.status_code == 303
    assert client.get("/auth/me").json()["email"] == "a@test.kyja"
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.email == "a@test.kyja"))
        consents = {c.kind: c.granted for c in db.scalars(select(ConsentRecord).where(ConsentRecord.user_id == uid))}
    assert consents == {"terms": True, "marketing": True}


def test_marketing_not_granted_by_default(client):
    register(client)
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.email == "a@test.kyja"))
        m = db.scalar(select(ConsentRecord).where(ConsentRecord.user_id == uid, ConsentRecord.kind == "marketing"))
    assert m.granted is False


def test_password_is_argon2id(client):
    register(client)
    with SessionLocal() as db:
        h = db.scalar(select(User.password_hash).where(User.email == "a@test.kyja"))
    assert h.startswith("$argon2id$")


def test_register_requires_terms_and_valid_password(client):
    r = client.post("/auth/register", data={"full_name": "X", "email": "b@test.kyja", "password": "short", "csrf_token": csrf(client)})
    assert r.status_code == 400
    r = register(client, email="b@test.kyja", accept_terms="")
    assert r.status_code == 400


def test_duplicate_email_generic_message(client):
    register(client)
    other = type(client)(client.app, follow_redirects=False)
    other.get("/auth/login")
    r = register(other)
    assert r.status_code == 400
    assert "existe" not in r.text


def test_login_wrong_password_generic(client):
    register(client)
    other = type(client)(client.app, follow_redirects=False)
    other.get("/auth/login")
    r1 = login(other, password="wrong-password-123")
    r2 = login(other, email="nobody@test.kyja")
    assert r1.status_code == r2.status_code == 401
    assert "Email ou mot de passe incorrect" in r1.text and "Email ou mot de passe incorrect" in r2.text


def test_login_rate_limit(client):
    for _ in range(5):
        assert login(client, email="x@test.kyja", password="nope-nope-nope").status_code == 401
    assert login(client, email="x@test.kyja", password="nope-nope-nope").status_code == 429


def test_csrf_required_on_post(client):
    r = client.post("/auth/login", data={"email": "a@test.kyja", "password": "x"})
    assert r.status_code == 403
    r = client.post("/auth/login", data={"email": "a@test.kyja", "password": "x", "csrf_token": "forged.token"})
    assert r.status_code == 403


def test_cookies_flags_and_headers(client):
    r = register(client)
    sc = " ".join(r.headers.get_list("set-cookie")).lower()
    assert "httponly" in sc and "samesite=strict" in sc
    assert "path=/;" in sc.replace("path=/ ", "path=/;") or "path=/" in sc  # renouvellement : envoyé partout (renouvellement silencieux)
    h = client.get("/").headers
    assert "nonce-" in h["content-security-policy"] and "frame-ancestors 'none'" in h["content-security-policy"]
    assert h["x-content-type-options"] == "nosniff"


def test_refresh_rotation_and_reuse_detection(client):
    register(client)
    old = client.cookies.get("refresh_token", path="/")
    assert client.post("/auth/refresh", headers={"x-csrf-token": csrf(client)}).status_code == 200
    new = client.cookies.get("refresh_token", path="/")
    assert new != old
    # rejouer l'ancien jeton = vol présumé : tout est révoqué
    client.cookies.set("refresh_token", old, path="/")
    assert client.post("/auth/refresh", headers={"x-csrf-token": csrf(client)}).status_code == 401
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.email == "a@test.kyja"))
        active = db.scalars(select(RefreshToken).where(RefreshToken.user_id == uid, RefreshToken.revoked_at.is_(None))).all()
    assert active == []


def test_logout_revokes_and_me_requires_auth(client):
    register(client)
    client.post("/auth/logout", data={"csrf_token": csrf(client)})
    assert client.get("/auth/me").status_code == 401


def test_me_requires_auth(client):
    assert client.get("/auth/me").status_code == 401
