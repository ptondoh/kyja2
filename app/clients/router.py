"""Profil de la cliente : informations, consentement, mot de passe, export, droit à l'oubli."""
import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import service as auth_service
from app.auth.deps import require_login
from app.auth.ratelimit import RateLimiter
from app.auth.security import hash_password, verify_password
from app.bookings import drafts
from app.clients import service
from app.db.base import get_db
from app.db.models.core import Appointment, Role, User
from app.security_middleware import verify_csrf
from app.templating import render

router = APIRouter(prefix="/clients", dependencies=[Depends(verify_csrf)])

PHONE_RE = re.compile(r"^[0-9 ()+.\-]{5,50}$")
MIN_PASSWORD, MAX_PASSWORD = 10, 128

# Vérifications du mot de passe sur les pages sensibles : 5 échecs / 15 min par compte (anti force brute).
sensitive_limiter = RateLimiter(max_hits=5, window_seconds=15 * 60)


def _profile_context(db: Session, user: User, **extra) -> dict:
    state = service.marketing_state(db, user.id)
    total = db.scalar(select(func.count()).select_from(Appointment).where(Appointment.client_id == user.id))
    return {
        "marketing": bool(state and state.granted),
        "marketing_since": state.created_at if state else None,
        "appointment_count": total,
        "can_forget": user.role == Role.client,
        "forget_word": service.FORGET_CONFIRMATION,
        **extra,
    }


def _page(request, db, user, status_code=200, **extra):
    return render(request, "clients/profile.html", status_code=status_code, current_user=user, **_profile_context(db, user, **extra))


def _password_ok(user: User, password: str) -> bool:
    """Vérifie le mot de passe actuel avec limitation des essais (même protection que la connexion)."""
    key = f"user:{user.id}"
    if sensitive_limiter.is_limited(key):
        return False
    if verify_password(user.password_hash, password):
        sensitive_limiter.reset(key)
        return True
    sensitive_limiter.hit(key)
    return False


@router.get("/me")
def profile(request: Request, db: Session = Depends(get_db), user: User = Depends(require_login)):
    return _page(request, db, user, saved=request.query_params.get("saved"))


@router.post("/me")
def update_profile(
    request: Request,
    full_name: str = Form(""),
    phone: str = Form(""),
    address: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    full_name, phone, address = full_name.strip(), phone.strip(), address.strip()
    errors = []
    if not full_name or len(full_name) > 200:
        errors.append("Le nom est requis (200 caractères maximum).")
    if phone and not PHONE_RE.match(phone):
        errors.append("Téléphone invalide (chiffres, espaces, + ( ) - . seulement).")
    if len(address) > 500:
        errors.append("L'adresse est trop longue (500 caractères maximum).")
    if errors:
        return _page(request, db, user, status_code=400, errors=errors, form={"full_name": full_name, "phone": phone, "address": address})
    user.full_name, user.phone, user.address = full_name, phone or None, address or None
    db.commit()
    return RedirectResponse("/clients/me?saved=profile", status_code=303)


@router.post("/me/consent")
def update_consent(
    marketing_optin: bool = Form(False), db: Session = Depends(get_db), user: User = Depends(require_login)
):
    service.set_marketing_consent(db, user, marketing_optin)  # jamais coché par défaut ; historique conservé
    return RedirectResponse("/clients/me?saved=consent", status_code=303)


@router.post("/me/password")
def change_password(
    request: Request,
    current_password: str = Form(""),
    new_password: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    if not _password_ok(user, current_password):
        return _page(request, db, user, status_code=400, errors=["Mot de passe actuel incorrect (ou trop d'essais : réessayez plus tard)."], section="password")
    if not (MIN_PASSWORD <= len(new_password) <= MAX_PASSWORD):
        return _page(request, db, user, status_code=400, errors=[f"Le nouveau mot de passe doit contenir entre {MIN_PASSWORD} et {MAX_PASSWORD} caractères."], section="password")
    user.password_hash = hash_password(new_password)
    db.commit()
    auth_service.revoke_all_tokens(db, user.id)  # tous les autres appareils sont déconnectés
    response = RedirectResponse("/clients/me?saved=password", status_code=303)
    auth_service.issue_session(db, user, response)  # cet appareil reste connecté
    return response


@router.get("/me/export")
def export_data(db: Session = Depends(get_db), user: User = Depends(require_login)):
    """Droit d'accès (LPRPDE) : copie de toutes les données de la cliente, sans secrets."""
    payload = service.export_user_data(db, user)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return Response(
        json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="kyja-mes-donnees-{stamp}.json"',
            "Cache-Control": "no-store",
        },
    )


@router.post("/me/delete")
def delete_account(
    request: Request,
    password: str = Form(""),
    confirmation: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    """Droit à l'oubli : anonymisation du compte (voir `service.forget_user`)."""
    if user.role != Role.client:
        return _page(request, db, user, status_code=403, errors=["Ce compte ne peut pas être supprimé ici : contactez le salon."], section="forget")
    if confirmation.strip() != service.FORGET_CONFIRMATION:
        return _page(request, db, user, status_code=400, errors=[f"Pour confirmer, écrivez {service.FORGET_CONFIRMATION}."], section="forget")
    if not _password_ok(user, password):
        return _page(request, db, user, status_code=400, errors=["Mot de passe incorrect (ou trop d'essais : réessayez plus tard)."], section="forget")
    service.forget_user(db, user)
    response = RedirectResponse("/clients/deleted", status_code=303)
    auth_service.clear_session(response)
    drafts.clear_cookie(response)
    return response


@router.get("/deleted")
def deleted(request: Request):
    return render(request, "clients/deleted.html", current_user=None)
