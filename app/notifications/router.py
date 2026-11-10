"""Alertes du site : liste, lecture, et interrogation légère pour l'alerte du navigateur."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user_optional, require_login
from app.db.base import get_db
from app.db.models.core import Notification, User
from app.pagination import paginate
from app.notifications import service
from app.security_middleware import verify_csrf
from app.templating import render

router = APIRouter(prefix="/notifications")


@router.get("")
def notifications_page(request: Request, db: Session = Depends(get_db), user: User = Depends(require_login)):
    pg = paginate(db, select(Notification).where(Notification.user_id == user.id).order_by(Notification.id.desc()), request.query_params.get("page"))
    return render(request, "notifications/list.html", current_user=user, items=pg.items, pg=pg)


@router.post("/read", dependencies=[Depends(verify_csrf)])
def mark_read(db: Session = Depends(get_db), user: User = Depends(require_login)):
    service.mark_all_read(db, user.id)
    return RedirectResponse("/notifications", status_code=303)


@router.get("/poll")
def poll(since: int = 0, db: Session = Depends(get_db), user: User | None = Depends(get_current_user_optional)):
    """Pour le petit script de la cloche. Ne renvoie que les alertes non lues, et seulement à la personne connectée.

    L'alerte du navigateur n'est montrée que si la personne l'a choisie dans son profil (le personnel : toujours)."""
    if user is None:
        return JSONResponse({"unread": 0, "items": [], "browser_alert": False}, headers={"Cache-Control": "no-store"})
    rows = db.scalars(
        select(Notification)
        .where(Notification.user_id == user.id, Notification.read_at.is_(None), Notification.id > max(since, 0))
        .order_by(Notification.id)
        .limit(10)
    ).all()
    return JSONResponse(
        {
            "unread": service.unread_count(db, user.id),
            "browser_alert": user.pref_browser_alert,
            "items": [{"id": n.id, "title": n.title, "body": n.body, "url": n.url} for n in rows],
        },
        headers={"Cache-Control": "no-store"},
    )
