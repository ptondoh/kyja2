import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import service
from app.auth.deps import REFRESH_COOKIE, get_current_user_optional, require_user
from app.auth.ratelimit import login_limiter, register_limiter
from app.auth.security import (
    DUMMY_HASH,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.db.base import get_db
from app.db.models.core import ConsentRecord, Role, User
from app.security_middleware import verify_csrf
from app.templating import render

router = APIRouter(prefix="/auth", dependencies=[Depends(verify_csrf)])

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
MIN_PASSWORD, MAX_PASSWORD = 10, 128
GENERIC_LOGIN_ERROR = "Email ou mot de passe incorrect."


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/login")
def login_page(request: Request, user: User | None = Depends(get_current_user_optional)):
    if user:
        return RedirectResponse("/", status_code=303)
    return render(request, "auth/login.html")


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = email.strip().lower()
    ip_key, email_key = f"ip:{_client_ip(request)}", f"email:{email}"

    if login_limiter.is_limited(ip_key) or login_limiter.is_limited(email_key):
        return render(
            request,
            "auth/login.html",
            status_code=429,
            error="Trop de tentatives. Réessayez dans 15 minutes.",
        )

    user = db.scalar(select(User).where(User.email == email))
    # On vérifie toujours un hachage pour que le temps de réponse ne révèle rien.
    ok = verify_password(user.password_hash if user else DUMMY_HASH, password)
    if not (user and ok and user.is_active and not user.anonymized_at):
        login_limiter.hit(ip_key)
        login_limiter.hit(email_key)
        return render(request, "auth/login.html", status_code=401, error=GENERIC_LOGIN_ERROR)

    login_limiter.reset(email_key)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        db.commit()
    response = RedirectResponse("/", status_code=303)
    service.issue_session(db, user, response)
    return response


@router.get("/register")
def register_page(request: Request):
    return render(request, "auth/register.html")


@router.post("/register")
def register(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    phone: str = Form(""),
    accept_terms: bool = Form(False),
    marketing_optin: bool = Form(False),
    db: Session = Depends(get_db),
):
    ip_key = f"ip:{_client_ip(request)}"
    if register_limiter.is_limited(ip_key):
        return render(
            request, "auth/register.html", status_code=429, error="Trop de demandes. Réessayez plus tard."
        )
    register_limiter.hit(ip_key)

    email = email.strip().lower()
    full_name = full_name.strip()
    error = None
    if not full_name or len(full_name) > 200:
        error = "Le nom est requis."
    elif not EMAIL_RE.match(email) or len(email) > 255:
        error = "Adresse email invalide."
    elif not (MIN_PASSWORD <= len(password) <= MAX_PASSWORD):
        error = f"Le mot de passe doit contenir entre {MIN_PASSWORD} et {MAX_PASSWORD} caractères."
    elif not accept_terms:
        error = "Vous devez accepter les conditions et la politique de confidentialité."
    elif db.scalar(select(User.id).where(User.email == email)):
        # Message générique : ne confirme pas qu'un compte existe déjà.
        error = "Impossible de créer le compte avec ces informations."
    if error:
        return render(
            request,
            "auth/register.html",
            status_code=400,
            error=error,
            form={"full_name": full_name, "email": email, "phone": phone},
        )

    user = User(
        email=email,
        password_hash=hash_password(password),
        role=Role.client,
        full_name=full_name,
        phone=phone.strip()[:50] or None,
    )
    db.add(user)
    db.flush()
    # Preuves de consentement (LPRPDE) : le marketing n'est jamais coché par défaut.
    db.add(ConsentRecord(user_id=user.id, kind="terms", granted=True))
    db.add(ConsentRecord(user_id=user.id, kind="marketing", granted=marketing_optin))
    db.commit()

    response = RedirectResponse("/", status_code=303)
    service.issue_session(db, user, response)
    return response


@router.post("/refresh")
def refresh(request: Request, db: Session = Depends(get_db)):
    raw = request.cookies.get(REFRESH_COOKIE)
    user = service.rotate_refresh_token(db, raw) if raw else None
    if not user:
        response = JSONResponse({"detail": "Session expirée"}, status_code=401)
        service.clear_session(response)
        return response
    response = JSONResponse({"status": "ok"})
    service.issue_session(db, user, response)
    return response


@router.post("/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    raw = request.cookies.get(REFRESH_COOKIE)
    if raw:
        service.revoke_refresh_token(db, raw)
    response = RedirectResponse("/", status_code=303)
    service.clear_session(response)
    return response


@router.post("/logout-all")
def logout_all(user: User = Depends(require_user), db: Session = Depends(get_db)):
    service.revoke_all_tokens(db, user.id)
    response = RedirectResponse("/", status_code=303)
    service.clear_session(response)
    return response


@router.get("/me")
def me(user: User = Depends(require_user)):
    return {"id": user.id, "email": user.email, "full_name": user.full_name, "role": user.role.value}
