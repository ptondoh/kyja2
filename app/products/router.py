from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import get_current_user_optional
from app.db.base import get_db
from app.db.models.core import Product, User
from app.services.router import ready_media
from app.templating import render

router = APIRouter(prefix="/products")


@router.get("")
def list_products(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    products = db.scalars(
        select(Product)
        .options(selectinload(Product.media))
        .where(Product.is_active.is_(True))
        .order_by(Product.name)
    ).all()
    return render(
        request,
        "catalog/products.html",
        current_user=user,
        products=[(p, ready_media(p.media)) for p in products],
    )


@router.get("/{product_id}")
def product_detail(
    request: Request,
    product_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    product = db.scalar(
        select(Product).options(selectinload(Product.media)).where(Product.id == product_id)
    )
    if not product or not product.is_active:
        raise HTTPException(status_code=404)
    return render(
        request,
        "catalog/product_detail.html",
        current_user=user,
        product=product,
        media=ready_media(product.media),
    )
