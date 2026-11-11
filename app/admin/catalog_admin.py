"""Catégories, et suppression / archivage des prestations et des produits (voir `app/services/lifecycle.py`)."""
import os
import tempfile
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from app.auth.deps import require_role
from app.db.base import get_db
from app.filters import apply_status, safe_next, status_choices
from app.pagination import like, paginate
from app.db.models.core import Category, Product, Role, Service, User
from app.security_middleware import verify_csrf
from app.services import lifecycle
from app.templating import render

router = APIRouter(prefix="/admin", dependencies=[Depends(require_role(Role.admin)), Depends(verify_csrf)])

NAME_MAX = 100


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _category_or_404(db: Session, category_id: int) -> Category:
    c = db.get(Category, category_id)
    if not c:
        raise HTTPException(status_code=404)
    return c


def _name_error(db: Session, raw: str, exclude_id: int | None = None) -> str | None:
    name = raw.strip()
    if not name or len(name) > NAME_MAX:
        return f"Le nom de la catégorie est requis ({NAME_MAX} caractères maximum)."
    q = select(Category.id).where(func.lower(Category.name) == name.lower())
    if exclude_id is not None:
        q = q.where(Category.id != exclude_id)
    return "Une catégorie porte déjà ce nom." if db.scalar(q) else None


def _list_context(db: Session, request: Request, **extra) -> dict:
    """Liste filtrable et paginée des catégories, avec le nombre de prestations de chacune."""
    params = request.query_params
    q, statut = (params.get("q") or "").strip(), (params.get("statut") or "").strip()
    query = select(Category).order_by(Category.name)
    if q:
        query = query.where(Category.name.ilike(like(q), escape="\\"))
    query = apply_status(query, Category.is_active, statut, feminine=True)
    pg = paginate(db, query, params.get("page"))
    counts = dict(
        db.execute(
            select(Service.category_id, func.count(Service.id)).where(Service.category_id.in_([c.id for c in pg.items])).group_by(Service.category_id)
        ).all()
    )
    rows = [(c, counts.get(c.id, 0)) for c in pg.items]
    return {"rows": rows, "pg": pg, "q": q, "statut": statut, "status_labels": list(status_choices(True).values()), **extra}


# --- Catégories -------------------------------------------------------------------------------------------------


@router.get("/categories")
def categories_list(request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))):
    return render(request, "admin/categories.html", current_user=user, **_list_context(db, request))


@router.post("/categories")
def category_create(
    request: Request, name: str = Form(""), db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))
):
    error = _name_error(db, name)
    if error:
        return render(request, "admin/categories.html", status_code=400, current_user=user, **_list_context(db, request, errors=[error], new_name=name))
    db.add(Category(name=name.strip()))
    db.commit()
    return _redirect("/admin/categories?saved=created")


@router.post("/categories/{category_id}")
def category_rename(
    request: Request,
    category_id: int,
    name: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.admin)),
):
    category = _category_or_404(db, category_id)
    error = _name_error(db, name, exclude_id=category.id)
    if error:
        return render(request, "admin/categories.html", status_code=400, current_user=user, **_list_context(db, request, errors=[error]))
    category.name = name.strip()
    db.commit()
    return _redirect("/admin/categories?saved=renamed")


@router.post("/categories/{category_id}/toggle")
def category_toggle(category_id: int, next: str = Form(""), db: Session = Depends(get_db)):
    """Catégorie désactivée : elle n'apparaît plus comme filtre sur le site (ses prestations restent visibles)."""
    category = _category_or_404(db, category_id)
    category.is_active = not category.is_active
    db.commit()
    return _redirect(safe_next(next, "/admin/categories"))


@router.get("/categories/{category_id}/delete")
def category_delete_page(
    request: Request, category_id: int, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))
):
    category = _category_or_404(db, category_id)
    return render(request, "admin/category_delete.html", current_user=user, category=category, overview=lifecycle.category_overview(db, category))


@router.get("/categories/{category_id}/backup")
def category_backup(category_id: int, db: Session = Depends(get_db)):
    """Sauvegarde téléchargeable (ZIP : prestations en JSON + photos et vidéos) à faire AVANT la suppression."""
    category = _category_or_404(db, category_id)
    fd, path = tempfile.mkstemp(suffix=".zip")
    os.close(fd)
    lifecycle.backup_category_zip(db, category, Path(path))
    return FileResponse(
        path,
        media_type="application/zip",
        filename=lifecycle.backup_filename(category),
        headers={"Cache-Control": "no-store"},
        background=BackgroundTask(os.unlink, path),
    )


@router.post("/categories/{category_id}/delete")
def category_delete(
    request: Request,
    category_id: int,
    confirmation: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.admin)),
):
    category = _category_or_404(db, category_id)
    overview = lifecycle.category_overview(db, category)

    def refuse(message: str):
        return render(
            request, "admin/category_delete.html", status_code=400, current_user=user,
            category=category, overview=overview, errors=[message],
        )  # fmt: skip

    if confirmation.strip().lower() != category.name.lower():
        return refuse("Pour confirmer, écrivez exactement le nom de la catégorie.")
    try:
        result = lifecycle.remove_category(db, category)
    except lifecycle.InUse as exc:
        return refuse("Des rendez-vous à venir utilisent : " + ", ".join(f"{n} ({c})" for n, c in exc.blockers) + ". Annulez-les d'abord.")
    return _redirect(f"/admin/categories?removed={len(result.deleted)}&archived={len(result.archived)}")


# --- Prestations : supprimer / archiver / restaurer -----------------------------------------------------------------


def _service_or_404(db: Session, service_id: int) -> Service:
    s = db.get(Service, service_id)
    if not s:
        raise HTTPException(status_code=404)
    return s


@router.get("/services/{service_id}/delete")
def service_delete_page(
    request: Request, service_id: int, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))
):
    service = _service_or_404(db, service_id)
    if service.is_custom_request:
        raise HTTPException(status_code=400, detail="Cette prestation est indispensable à la réservation sur mesure.")
    return render(
        request, "admin/service_delete.html", current_user=user, service=service,
        history=lifecycle.has_history(db, service_id=service.id), future=lifecycle.future_count(db, service_id=service.id),
    )  # fmt: skip


@router.post("/services/{service_id}/delete")
def service_delete(
    request: Request, service_id: int, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))
):
    service = _service_or_404(db, service_id)
    try:
        result = lifecycle.remove_service(db, service)
    except lifecycle.Protected:
        raise HTTPException(status_code=400, detail="Cette prestation est indispensable à la réservation sur mesure.") from None
    except lifecycle.InUse as exc:
        return render(
            request, "admin/service_delete.html", status_code=400, current_user=user, service=service,
            history=True, future=exc.blockers[0][1],
            errors=[f"{exc.blockers[0][1]} rendez-vous à venir utilisent cette prestation : annulez-les d'abord."],
        )  # fmt: skip
    return _redirect(f"/admin/services?removed={quote(','.join(result.deleted))}&archived={quote(','.join(result.archived))}")


@router.post("/services/{service_id}/restore")
def service_restore(service_id: int, db: Session = Depends(get_db)):
    lifecycle.restore_service(db, _service_or_404(db, service_id))
    return _redirect(f"/admin/services/{service_id}?saved=1")


# --- Produits : suppression définitive ---------------------------------------------------------------------------------


def _product_or_404(db: Session, product_id: int) -> Product:
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(status_code=404)
    return p


@router.get("/products/{product_id}/delete")
def product_delete_page(
    request: Request, product_id: int, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))
):
    return render(request, "admin/product_delete.html", current_user=user, product=_product_or_404(db, product_id))


@router.post("/products/{product_id}/delete")
def product_delete(product_id: int, db: Session = Depends(get_db)):
    product = _product_or_404(db, product_id)
    name = product.name
    lifecycle.remove_product(db, product)
    return _redirect(f"/admin/products?removed={quote(name)}")
