"""Liste des utilisateurs et de leurs coordonnées (administration)."""
from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.auth.deps import require_role
from app.db.base import get_db
from app.db.models.core import ConsentRecord, Role, User
from app.filters import apply_status, matching, status_choices
from app.marketing.service import latest_marketing_consent
from app.pagination import like, paginate
from app.security_middleware import verify_csrf
from app.templating import render

router = APIRouter(prefix="/admin", dependencies=[Depends(require_role(Role.admin)), Depends(verify_csrf)])

ROLE_LABELS = {"client": "Cliente", "hairdresser": "Coiffeuse", "admin": "Administrateur"}
PROMO_LABELS = {"oui": "Accepte", "non": "N'accepte pas"}  # « n'accepte pas » inclut celles qui n'ont jamais répondu


@router.get("/users")
def users_list(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    params = request.query_params
    f = {k: (params.get(k) or "").strip() for k in ("q", "role", "statut", "promo")}
    query = select(User).order_by(User.id.desc())
    if f["q"]:
        pattern = like(f["q"])
        query = query.where(or_(User.full_name.ilike(pattern, escape="\\"), User.email.ilike(pattern, escape="\\"), User.phone.ilike(pattern, escape="\\")))
    by_role = matching(f["role"], ROLE_LABELS)
    if by_role is not None:
        query = query.where(User.role.in_([Role(r) for r in by_role]))
    query = apply_status(query, User.is_active, f["statut"])
    by_promo = matching(f["promo"], PROMO_LABELS)
    if by_promo is not None and len(by_promo) == 1:
        accepted = latest_marketing_consent()
        query = query.where(User.role == Role.client, accepted.is_(True) if by_promo == ["oui"] else accepted.is_not(True))
    elif by_promo == []:
        query = query.where(User.id < 0)
    pg = paginate(db, query, params.get("page"))
    consent: dict[int, bool] = {}
    for rec in db.scalars(select(ConsentRecord).where(ConsentRecord.kind == "marketing", ConsentRecord.user_id.in_([u.id for u in pg.items])).order_by(ConsentRecord.id)):
        consent[rec.user_id] = rec.granted  # le dernier enregistrement l'emporte
    return render(
        request, "admin/users.html", current_user=user, items=pg.items, pg=pg, f=f, consent=consent, role_labels=ROLE_LABELS,
        roles=list(ROLE_LABELS.values()), promos=list(PROMO_LABELS.values()), status_labels=list(status_choices().values()),
    )  # fmt: skip
