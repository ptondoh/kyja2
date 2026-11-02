from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import get_current_user_optional
from app.db.base import get_db
from app.db.models.core import Category, Media, MediaStatus, Service, User
from app.templating import render

router = APIRouter(prefix="/services")


def ready_media(items: list[Media]) -> list[Media]:
    """Seuls les médias traités sont visibles côté public."""
    return [m for m in items if m.status == MediaStatus.ready]


@router.get("")
def list_services(
    request: Request,
    category: str | None = None,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    visible = (Service.is_active.is_(True), Service.is_custom_request.is_(False), Service.archived_at.is_(None))
    q = select(Service).options(selectinload(Service.media)).where(*visible)
    if category:
        q = q.join(Service.category).where(func.lower(Category.name) == category.strip().lower())
    # Regroupées par catégorie (sans catégorie à la fin), puis par nom.
    services = sorted(db.scalars(q).unique().all(), key=lambda s: (s.category is None, s.category.name.lower() if s.category else "", s.name.lower()))
    categories = db.scalars(
        select(Category.name).join(Service, Service.category_id == Category.id).where(*visible).distinct().order_by(Category.name)
    ).all()
    return render(
        request,
        "catalog/services.html",
        current_user=user,
        services=[(s, ready_media(s.media)) for s in services],
        categories=categories,
        active_category=category,
    )


@router.get("/{service_id}")
def service_detail(
    request: Request,
    service_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    service = db.scalar(
        select(Service).options(selectinload(Service.media)).where(Service.id == service_id)
    )
    if not service or not service.is_active or service.is_custom_request or service.archived_at:
        raise HTTPException(status_code=404)
    return render(
        request,
        "catalog/service_detail.html",
        current_user=user,
        service=service,
        media=ready_media(service.media),
    )
