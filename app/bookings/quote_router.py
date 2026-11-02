"""Réponse de la cliente au prix proposé : par le lien reçu (sans connexion) ou depuis son rendez-vous."""
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth.deps import require_login
from app.auth.ratelimit import RateLimiter
from app.bookings import quotes
from app.bookings.quotes import QuoteError
from app.db.base import get_db
from app.db.models.core import Appointment, User
from app.notifications import messages
from app.notifications import service as notifications
from app.security_middleware import verify_csrf
from app.templating import render

router = APIRouter(dependencies=[Depends(verify_csrf)])

# Devinettes de jetons : 20 essais ratés / heure / adresse IP (un jeton valide fait 256 bits, c'est surtout un garde-fou).
token_limiter = RateLimiter(max_hits=20, window_seconds=3600)


def _tell_staff(db: Session, appt: Appointment, accepted: bool) -> None:
    title, body = messages.staff_quote_decision(appt, accepted)
    notifications.notify_staff(db, appt, kind="quote_accepted" if accepted else "quote_declined", title=title, body=body)


@router.get("/quotes/{token}")
def quote_page(request: Request, token: str, db: Session = Depends(get_db)):
    ip = request.client.host if request.client else "?"
    if token_limiter.is_limited(ip):
        raise HTTPException(status_code=429, detail="Trop d'essais. Réessayez plus tard.")
    appt = quotes.find_by_token(db, token)
    if appt is None:
        token_limiter.hit(ip)
        return render(request, "bookings/quote.html", status_code=404, appt=None, open=False)
    response = render(request, "bookings/quote.html", appt=appt, open=quotes.is_open(appt), token=token, done=None)
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"  # le jeton est dans l'adresse
    return response


@router.post("/quotes/{token}")
def quote_answer(
    request: Request,
    token: str,
    decision: str = Form(...),
    db: Session = Depends(get_db),
):
    ip = request.client.host if request.client else "?"
    if token_limiter.is_limited(ip):
        raise HTTPException(status_code=429, detail="Trop d'essais. Réessayez plus tard.")
    appt = quotes.find_by_token(db, token)
    if appt is None or decision not in ("accept", "decline"):
        token_limiter.hit(ip)
        return render(request, "bookings/quote.html", status_code=404, appt=None, open=False)
    try:
        quotes.decide(db, appt.id, decision == "accept")
    except QuoteError:
        return render(request, "bookings/quote.html", status_code=409, appt=appt, open=False, token=token)
    db.refresh(appt)
    _tell_staff(db, appt, decision == "accept")
    # Le jeton est invalidé par la décision : on affiche directement la confirmation (pas de redirection vers le lien).
    response = render(request, "bookings/quote.html", appt=appt, open=False, token=None, done=decision)
    response.headers["Cache-Control"] = "no-store"
    return response


@router.post("/bookings/{appointment_id}/quote")
def quote_answer_logged_in(
    appointment_id: int,
    decision: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_login),
):
    appt = db.get(Appointment, appointment_id)
    if appt is None or appt.client_id != user.id or decision not in ("accept", "decline"):
        raise HTTPException(status_code=404)
    try:
        quotes.decide(db, appt.id, decision == "accept")
    except QuoteError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from None
    db.refresh(appt)
    _tell_staff(db, appt, decision == "accept")
    return RedirectResponse(f"/bookings/{appt.id}?answered={decision}", status_code=303)
