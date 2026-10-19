from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import get_current_user_optional
from app.db.base import get_db
from app.db.models.core import Media, MediaStatus, Service, User
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
    q = select(Service).options(selectinload(Service.media)).where(
        Service.is_active.is_(True), Service.is_custom_request.is_(False)
    )
    if category:
        q = q.where(Service.category == category)
    services = db.scalars(q.order_by(Service.category, Service.name)).all()
    categories = db.scalars(
        select(Service.category)
        .where(Service.is_active.is_(True), Service.is_custom_request.is_(False), Service.category.is_not(None))
        .distinct()
        .order_by(Service.category)
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
    if not service or not service.is_active or service.is_custom_request:
        raise HTTPException(status_code=404)
    return render(
        request,
        "catalog/service_detail.html",
        current_user=user,
        service=service,
        media=ready_media(service.media),
    )
