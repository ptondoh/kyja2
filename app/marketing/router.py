"""Administration des promotions (/admin/marketing) et page publique de désabonnement (/unsubscribe/…)."""
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.auth.deps import require_role
from app.clients import service as clients
from app.db.base import get_db
from app.db.models.core import MarketingCampaign, MessageOutbox, Role, User
from app.filters import matching
from app.marketing import service
from app.pagination import paginate
from app.security_middleware import verify_csrf
from app.tasks import dispatch
from app.templating import render

CHUNK = 50  # messages confiés à une tâche à la fois
STATUS_LABELS = {"draft": "Brouillon", "sent": "Envoyée"}

router = APIRouter(prefix="/admin/marketing", dependencies=[Depends(require_role(Role.admin)), Depends(verify_csrf)])
public = APIRouter()


def _campaign_or_404(db: Session, campaign_id: int) -> MarketingCampaign:
    c = db.get(MarketingCampaign, campaign_id)
    if not c:
        raise HTTPException(status_code=404)
    return c


def _stats(db: Session, campaign_id: int) -> dict[str, int]:
    rows = db.execute(select(MessageOutbox.status, func.count()).where(MessageOutbox.campaign_id == campaign_id).group_by(MessageOutbox.status)).all()
    return {status: n for status, n in rows}


def _review(request, db, user, campaign, status_code=200, errors=None):
    return render(
        request, "admin/marketing_review.html", status_code=status_code, current_user=user, campaign=campaign,
        audience=service.audience(db), blockers=service.send_blockers(db, campaign), stats=_stats(db, campaign.id), errors=errors or [],
        test_sent=request.query_params.get("test") == "1",
    )  # fmt: skip


@router.get("")
def campaigns(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    statut = (request.query_params.get("statut") or "").strip()
    query = select(MarketingCampaign).order_by(MarketingCampaign.id.desc())
    found = matching(statut, STATUS_LABELS)
    if found is not None:
        query = query.where(MarketingCampaign.status.in_(found))
    pg = paginate(db, query, request.query_params.get("page"))
    return render(
        request, "admin/marketing.html", current_user=user, items=pg.items, pg=pg, statut=statut,
        status_labels=list(STATUS_LABELS.values()), audience=service.audience(db),
    )  # fmt: skip


@router.get("/new")
def new(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    return render(request, "admin/marketing_form.html", current_user=user, audience=service.audience(db), f={"send_email": True}, errors=[])


@router.post("/review")
def review(
    request: Request,
    subject: str = Form(""),
    body: str = Form(""),
    sms_text: str = Form(""),
    send_email: bool = Form(False),
    send_sms: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.admin)),
):
    errors = service.validate(subject, body, sms_text, send_email, send_sms, db)
    if errors:
        f = {"subject": subject, "body": body, "sms_text": sms_text, "send_email": send_email, "send_sms": send_sms}
        return render(request, "admin/marketing_form.html", status_code=400, current_user=user, audience=service.audience(db), f=f, errors=errors)
    campaign = MarketingCampaign(
        subject=subject.strip(), body=body.strip(), sms_text=sms_text.strip() or None, send_email=send_email, send_sms=send_sms, created_by_id=user.id,
    )
    db.add(campaign)
    db.commit()
    return RedirectResponse(f"/admin/marketing/{campaign.id}", status_code=303)


@router.get("/{campaign_id}")
def detail(campaign_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    return _review(request, db, user, _campaign_or_404(db, campaign_id))


@router.post("/{campaign_id}/test")
def test(campaign_id: int, background: BackgroundTasks, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    """Envoie un exemplaire à l'administrateur seulement, pour voir le message tel que la cliente le recevra."""
    campaign = _campaign_or_404(db, campaign_id)
    ids = service.queue_test(db, campaign, user)
    background.add_task(dispatch.send_messages, ids)
    return RedirectResponse(f"/admin/marketing/{campaign.id}?test=1", status_code=303)


@router.post("/{campaign_id}/send")
def send(
    campaign_id: int,
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.admin)),
):
    campaign = _campaign_or_404(db, campaign_id)
    blockers = service.send_blockers(db, campaign)
    if blockers:
        return _review(request, db, user, campaign, status_code=400, errors=blockers)
    # « réserver » l'envoi : un double clic ou deux onglets ne peuvent pas envoyer deux fois
    claimed = db.execute(update(MarketingCampaign).where(MarketingCampaign.id == campaign.id, MarketingCampaign.status == "draft").values(status="sending")).rowcount
    db.commit()
    if claimed:
        db.refresh(campaign)
        ids = service.queue_campaign(db, campaign)
        for i in range(0, len(ids), CHUNK):
            background.add_task(dispatch.send_messages, ids[i : i + CHUNK])
    return RedirectResponse(f"/admin/marketing/{campaign.id}", status_code=303)


# --- désabonnement (public, sans connexion) -----------------------------------------------------------------------------


@public.get("/unsubscribe/{token}")
def unsubscribe_page(token: str, request: Request, db: Session = Depends(get_db)):
    person = service.user_from_token(db, token)
    return render(request, "marketing/unsubscribe.html", status_code=200 if person else 404, valid=bool(person), done=False, token=token)


@public.post("/unsubscribe/{token}")
def unsubscribe(token: str, request: Request, db: Session = Depends(get_db), _: None = Depends(verify_csrf)):
    person = service.user_from_token(db, token)
    if person:
        clients.set_marketing_consent(db, person, False)  # historique ajouté, jamais modifié
    return render(request, "marketing/unsubscribe.html", status_code=200 if person else 404, valid=bool(person), done=bool(person), token=token)
