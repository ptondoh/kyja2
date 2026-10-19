"""Renouvellement silencieux de la session : fini la déconnexion toutes les 15 minutes."""
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app.auth.security import hash_refresh_token
from app.config import get_settings
from app.db.base import SessionLocal
from app.db.models.core import RefreshToken, User
from app.main import app
from tests.conftest import csrf, login, register


def fresh_client():
    c = TestClient(app, follow_redirects=False)
    c.get("/auth/login")
    return c


def signed_in(email="a@test.kyja"):
    c = fresh_client()
    register(c, email=email)
    return c


def refresh_value(c) -> str:
    return c.cookies.get("refresh_token")


def tokens(email="a@test.kyja"):
    with SessionLocal() as db:
        uid = db.scalar(select(User.id).where(User.email == email))
        return db.scalars(select(RefreshToken).where(RefreshToken.user_id == uid).order_by(RefreshToken.id)).all()


def expire_access(c):
    """Ce que fait le navigateur au bout de 15 minutes : le cookie d'accès disparaît."""
    c.cookies.delete("access_token")


def client_with_only_refresh(raw: str) -> TestClient:
    c = fresh_client()
    c.cookies.set("refresh_token", raw, path="/")
    return c


# --- le cas normal ----------------------------------------------------------------------------------


def test_expired_access_cookie_is_renewed_silently():
    c = signed_in()
    old_refresh = refresh_value(c)
    expire_access(c)
    r = c.get("/auth/me")  # sans ce correctif : 401, déconnectée
    assert r.status_code == 200 and r.json()["email"] == "a@test.kyja"
    set_cookie = " ".join(r.headers.get_list("set-cookie")).lower()
    assert "access_token=" in set_cookie and "refresh_token=" in set_cookie  # les deux sont renouvelés
    assert refresh_value(c) != old_refresh  # rotation
    assert c.get("/auth/me").status_code == 200  # et la session suit avec les nouveaux cookies


def test_an_expired_jwt_is_renewed_too_not_only_a_missing_cookie():
    c = signed_in()
    s = get_settings()
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    expired = jwt.encode({"sub": "1", "role": "client", "type": "access", "exp": past}, s.secret_key, algorithm="HS256")
    c.cookies.set("access_token", expired, path="/")
    assert c.get("/auth/me").status_code == 200


def test_every_page_works_after_expiry_not_just_the_api():
    c = signed_in()
    expire_access(c)
    page = c.get("/bookings")  # une vraie page, derrière connexion
    assert page.status_code == 200 and "Mes rendez-vous" in page.text and "Test User" in page.text


def test_post_after_expiry_is_not_lost():
    """Le cas de l'admin : il remplit un long formulaire, la session expire, il enregistre."""
    from tests.conftest import make_admin

    c = fresh_client()
    make_admin(c)
    expire_access(c)
    r = c.post("/admin/settings", data={"csrf_token": csrf(c), "video_max_mb": "90", "video_max_seconds": "180",
                                        "calendar_step_min": "15", "booking_horizon_days": "60", "booking_min_notice_min": "60"})  # fmt: skip
    assert r.status_code == 303  # enregistré, pas de redirection vers la connexion


def test_session_is_renewed_only_when_needed():
    c = signed_in()
    r = c.get("/auth/me")  # accès encore valide
    assert r.status_code == 200 and not r.headers.get_list("set-cookie")  # aucun cookie inutile
    assert len(tokens()) == 1  # pas de rotation superflue


def test_refresh_window_slides_for_an_active_user():
    c = signed_in()
    expire_access(c)
    c.get("/auth/me")
    newest = tokens()[-1]
    assert newest.expires_at - datetime.now(timezone.utc) > timedelta(days=29)  # 30 jours repartent à zéro


def test_cookie_flags_and_scope():
    c = signed_in()
    expire_access(c)
    r = c.get("/auth/me")
    for header in r.headers.get_list("set-cookie"):
        low = header.lower()
        assert "httponly" in low and "samesite=strict" in low and "path=/" in low
    refresh = next(h for h in r.headers.get_list("set-cookie") if h.startswith("refresh_token="))
    assert "max-age=2592000" in refresh.lower()  # 30 jours


# --- requêtes simultanées : une page charge plusieurs ressources en parallèle ---------------------------------


def test_concurrent_requests_with_the_same_old_refresh_do_not_log_the_user_out():
    c = signed_in()
    old = refresh_value(c)
    first = client_with_only_refresh(old)
    second = client_with_only_refresh(old)  # même ancien jeton, quelques millisecondes plus tard
    r1, r2 = first.get("/auth/me"), second.get("/auth/me")
    assert r1.status_code == 200 and r2.status_code == 200  # la deuxième n'est pas prise pour un vol
    h1 = " ".join(r1.headers.get_list("set-cookie")).lower()
    h2 = " ".join(r2.headers.get_list("set-cookie")).lower()
    assert "refresh_token=" in h1  # la première fait la rotation
    assert "refresh_token=" not in h2 and "access_token=" in h2  # la seconde reçoit seulement un accès
    # la session issue de la rotation reste valable : aucune révocation en cascade
    expire_access(first)
    assert first.get("/auth/me").status_code == 200
    assert all(t.revoked_at is None for t in tokens()[-1:])


def test_replay_long_after_rotation_is_treated_as_theft():
    c = signed_in()
    old = refresh_value(c)
    expire_access(c)
    c.get("/auth/me")  # rotation : `old` est maintenant révoqué
    new = refresh_value(c)
    with SessionLocal() as db:  # le jeton a été révoqué il y a une minute (hors tolérance)
        db.execute(update(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(old))
                   .values(revoked_at=datetime.now(timezone.utc) - timedelta(minutes=1)))  # fmt: skip
        db.commit()
    thief = client_with_only_refresh(old)
    r = thief.get("/auth/me")
    assert r.status_code == 401  # le voleur n'obtient rien
    assert "refresh_token=" in " ".join(r.headers.get_list("set-cookie")).lower()  # et son cookie est effacé
    victim = client_with_only_refresh(new)
    assert victim.get("/auth/me").status_code == 401  # toutes les sessions de l'utilisatrice sont coupées
    assert all(t.revoked_at is not None for t in tokens())


# --- ce que le renouvellement ne doit jamais faire --------------------------------------------------------------


def test_logout_is_never_undone_by_the_renewal():
    c = signed_in()
    expire_access(c)
    r = c.post("/auth/logout", data={"csrf_token": csrf(c)})
    assert r.status_code == 303
    cookies = " ".join(r.headers.get_list("set-cookie")).lower()
    assert 'access_token=""' in cookies or "access_token=;" in cookies  # supprimés, pas ré-émis
    assert "max-age=0" in cookies
    assert c.get("/auth/me").status_code == 401
    assert all(t.revoked_at is not None for t in tokens())  # le jeton de renouvellement est révoqué


def test_logout_all_also_ends_every_session():
    c = signed_in()
    other = fresh_client()
    other.post("/auth/login", data={"email": "a@test.kyja", "password": "correct horse battery", "csrf_token": csrf(other)})
    c.post("/auth/logout-all", data={"csrf_token": csrf(c)})
    expire_access(other)
    assert other.get("/auth/me").status_code == 401  # l'autre appareil ne peut plus se renouveler


def test_static_files_do_not_trigger_a_renewal():
    c = signed_in()
    expire_access(c)
    before = len(tokens())
    r = c.get("/static/css/style.css")
    assert r.status_code == 200 and not r.headers.get_list("set-cookie")
    assert len(tokens()) == before and tokens()[-1].revoked_at is None  # aucune rotation pour une feuille de style


def test_unknown_expired_or_inactive_refresh_gives_a_visitor():
    assert client_with_only_refresh("pas-un-vrai-jeton-" + "x" * 30).get("/auth/me").status_code == 401
    c = signed_in()
    old = refresh_value(c)
    with SessionLocal() as db:
        db.execute(update(RefreshToken).values(expires_at=datetime.now(timezone.utc) - timedelta(days=1)))
        db.commit()
    assert client_with_only_refresh(old).get("/auth/me").status_code == 401  # 30 jours écoulés
    c2 = signed_in("b@test.kyja")
    with SessionLocal() as db:
        db.execute(update(User).where(User.email == "b@test.kyja").values(is_active=False))
        db.commit()
    expire_access(c2)
    assert c2.get("/auth/me").status_code == 401  # un compte désactivé ne se renouvelle pas


def test_a_visitor_without_cookies_costs_nothing():
    v = fresh_client()
    r = v.get("/services")
    assert r.status_code == 200 and "refresh_token" not in " ".join(r.headers.get_list("set-cookie"))


def test_explicit_refresh_endpoint_stays_strict():
    c = signed_in()
    old = refresh_value(c)
    assert c.post("/auth/refresh", headers={"x-csrf-token": csrf(c)}).status_code == 200
    replay = client_with_only_refresh(old)
    replay.get("/auth/login")
    # le rejeu immédiat via l'API explicite coupe tout : pas de tolérance sur cette route (clients mobiles)
    assert replay.post("/auth/refresh", headers={"x-csrf-token": csrf(replay)}).status_code == 401
    assert all(t.revoked_at is not None for t in tokens())


def test_password_login_still_issues_both_cookies():
    c = signed_in()
    other = fresh_client()
    r = login(other)
    assert r.status_code == 303
    names = {h.split("=")[0] for h in r.headers.get_list("set-cookie")}
    assert {"access_token", "refresh_token"} <= names
