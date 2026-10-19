import re
from decimal import Decimal, InvalidOperation

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.auth.deps import require_role
from app.config import get_settings
from app.db.base import get_db
from app.db.models.core import Media, PricingMode, Product, Role, Service, ServiceResource, User
from app.db.models.settings import get_int_setting, set_setting
from app.media import service as media_service
from app.security_middleware import verify_csrf
from app.templating import render


async def limit_body_size(request: Request, db: Session = Depends(get_db)) -> None:
    """Refuse tôt les corps trop gros, avant de lire le formulaire (Nginx reste le vrai garde-fou)."""
    cap = max(get_int_setting(db, "video_max_mb"), get_settings().image_max_mb) * 2**20 + 2**20
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > cap:
        raise HTTPException(status_code=413, detail="Fichier trop volumineux")


router = APIRouter(
    prefix="/admin",
    dependencies=[Depends(require_role(Role.admin)), Depends(limit_body_size), Depends(verify_csrf)],
)


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


def _decimal(raw: str, label: str, errors: list[str], lo=Decimal("0"), hi=Decimal("99999.99")):
    try:
        v = Decimal(raw.replace(",", ".").strip())
    except (InvalidOperation, AttributeError):
        errors.append(f"{label} invalide.")
        return None
    if not (lo <= v <= hi) or v != v.quantize(Decimal("0.01")):
        errors.append(f"{label} doit être entre {lo} et {hi} (2 décimales max).")
        return None
    return v


def _int(raw: str, label: str, lo: int, hi: int, errors: list[str]):
    try:
        v = int(raw)
    except (TypeError, ValueError):
        errors.append(f"{label} invalide.")
        return None
    if not (lo <= v <= hi):
        errors.append(f"{label} doit être entre {lo} et {hi}.")
        return None
    return v


def _name(raw: str, errors: list[str]) -> str:
    name = raw.strip()
    if not name or len(name) > 200:
        errors.append("Le nom est requis (200 caractères max).")
    return name


@router.get("")
def dashboard(request: Request, user: User = Depends(require_role(Role.admin))):
    return render(request, "admin/index.html", current_user=user)


# --- Réglages ---------------------------------------------------------------


@router.get("/settings")
def settings_page(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))
):
    return render(
        request,
        "admin/settings.html",
        current_user=user,
        video_max_mb=get_int_setting(db, "video_max_mb"),
        video_max_seconds=get_int_setting(db, "video_max_seconds"),
        calendar_step_min=get_int_setting(db, "calendar_step_min"),
        booking_horizon_days=get_int_setting(db, "booking_horizon_days"),
        booking_min_notice_min=get_int_setting(db, "booking_min_notice_min"),
    )


@router.post("/settings")
def settings_save(
    request: Request,
    video_max_mb: str = Form(...),
    video_max_seconds: str = Form(...),
    calendar_step_min: str = Form("15"),
    booking_horizon_days: str = Form("60"),
    booking_min_notice_min: str = Form("60"),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.admin)),
):
    errors: list[str] = []
    mb = _int(video_max_mb, "Taille maximale (Mo)", 1, 2000, errors)
    sec = _int(video_max_seconds, "Durée maximale (s)", 10, 3600, errors)
    step = _int(calendar_step_min, "Pas du calendrier (min)", 5, 60, errors)
    if step is not None and 60 % step:
        errors.append("Le pas du calendrier doit diviser 60 (5, 10, 15, 20, 30 ou 60).")
    horizon = _int(booking_horizon_days, "Réservation possible jusqu'à (jours)", 1, 365, errors)
    notice = _int(booking_min_notice_min, "Délai minimal avant un rendez-vous (min)", 0, 10080, errors)
    if errors:
        return render(
            request,
            "admin/settings.html",
            status_code=400,
            current_user=user,
            errors=errors,
            video_max_mb=video_max_mb,
            video_max_seconds=video_max_seconds,
            calendar_step_min=calendar_step_min,
            booking_horizon_days=booking_horizon_days,
            booking_min_notice_min=booking_min_notice_min,
        )
    set_setting(db, "video_max_mb", str(mb))
    set_setting(db, "video_max_seconds", str(sec))
    set_setting(db, "calendar_step_min", str(step))
    set_setting(db, "booking_horizon_days", str(horizon))
    set_setting(db, "booking_min_notice_min", str(notice))
    return _redirect("/admin/settings?saved=1")


# --- Services ---------------------------------------------------------------


@router.get("/services")
def services_list(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))
):
    items = db.scalars(select(Service).order_by(Service.name)).all()
    return render(request, "admin/services.html", current_user=user, items=items)


@router.get("/services/new")
def service_new(request: Request, user: User = Depends(require_role(Role.admin))):
    return render(request, "admin/service_form.html", current_user=user, item=None, media=[])


KIND_RE = re.compile(r"^[a-z0-9_]{1,50}$")


def _kinds(raw: str, errors: list[str]) -> list[str]:
    kinds = sorted({k.strip().lower() for k in raw.split(",") if k.strip()})
    if any(not KIND_RE.match(k) for k in kinds):
        errors.append("Équipements requis : lettres minuscules, chiffres et _ seulement (ex: wash_basin).")
    return kinds


def _service_fields(name, category, description, duration, buffer, price, pricing_mode, errors):
    mode = PricingMode.quote if pricing_mode == "quote" else PricingMode.estimate
    return {
        "name": _name(name, errors),
        "category": category.strip()[:100] or None,
        "description": description.strip() or None,
        "base_duration_min": _int(duration, "Durée", 5, 600, errors),
        "buffer_min": _int(buffer or "0", "Temps de repos", 0, 180, errors),
        "pricing_mode": mode,
        # Sans prix préétabli (« sur devis »), aucun montant n'est stocké ni promis.
        "price": _decimal(price, "Prix estimatif", errors) if mode == PricingMode.estimate else None,
    }


@router.post("/services")
@router.post("/services/{service_id}")
def service_save(
    request: Request,
    service_id: int | None = None,
    name: str = Form(""),
    category: str = Form(""),
    description: str = Form(""),
    base_duration_min: str = Form(""),
    buffer_min: str = Form("0"),
    price: str = Form(""),
    pricing_mode: str = Form("estimate"),
    required_kinds: str = Form(""),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.admin)),
):
    item = db.get(Service, service_id) if service_id else None
    if service_id and not item:
        raise HTTPException(status_code=404)
    errors: list[str] = []
    fields = _service_fields(name, category, description, base_duration_min, buffer_min, price, pricing_mode, errors)
    kinds = _kinds(required_kinds, errors)
    if errors:
        form = {**fields, "name": name, "price": price, "base_duration_min": base_duration_min,
                "buffer_min": buffer_min, "is_active": is_active, "pricing_mode": pricing_mode,
                "kinds": required_kinds}  # fmt: skip
        return render(
            request, "admin/service_form.html", status_code=400, current_user=user,
            item=item, form=form, errors=errors, media=item.media if item else [],
        )  # fmt: skip
    if not item:
        item = Service()
        db.add(item)
    for k, v in fields.items():
        setattr(item, k, v)
    item.is_active = is_active
    db.flush()
    item.resource_types = [ServiceResource(service_id=item.id, resource_kind=k) for k in kinds]
    db.commit()
    return _redirect(f"/admin/services/{item.id}?saved=1")


@router.get("/services/{service_id}")
def service_edit(
    request: Request,
    service_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.admin)),
):
    item = db.scalar(
        select(Service).options(selectinload(Service.media)).where(Service.id == service_id)
    )
    if not item:
        raise HTTPException(status_code=404)
    return render(
        request, "admin/service_form.html", current_user=user, item=item, media=item.media
    )


# --- Produits ---------------------------------------------------------------


@router.get("/products")
def products_list(
    request: Request, db: Session = Depends(get_db), user: User = Depends(require_role(Role.admin))
):
    items = db.scalars(select(Product).order_by(Product.name)).all()
    return render(request, "admin/products.html", current_user=user, items=items)


@router.get("/products/new")
def product_new(request: Request, user: User = Depends(require_role(Role.admin))):
    return render(request, "admin/product_form.html", current_user=user, item=None, media=[])


@router.post("/products")
@router.post("/products/{product_id}")
def product_save(
    request: Request,
    product_id: int | None = None,
    name: str = Form(""),
    description: str = Form(""),
    price: str = Form(""),
    stock: str = Form("0"),
    is_active: bool = Form(False),
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.admin)),
):
    item = db.get(Product, product_id) if product_id else None
    if product_id and not item:
        raise HTTPException(status_code=404)
    errors: list[str] = []
    fields = {
        "name": _name(name, errors),
        "description": description.strip() or None,
        "price": _decimal(price, "Prix", errors),
        "stock": _int(stock or "0", "Stock", 0, 1_000_000, errors),
    }
    if errors:
        form = {"name": name, "description": description, "price": price, "stock": stock,
                "is_active": is_active}  # fmt: skip
        return render(
            request, "admin/product_form.html", status_code=400, current_user=user,
            item=item, form=form, errors=errors, media=item.media if item else [],
        )  # fmt: skip
    if not item:
        item = Product()
        db.add(item)
    for k, v in fields.items():
        setattr(item, k, v)
    item.is_active = is_active
    db.commit()
    return _redirect(f"/admin/products/{item.id}?saved=1")


@router.get("/products/{product_id}")
def product_edit(
    request: Request,
    product_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_role(Role.admin)),
):
    item = db.scalar(
        select(Product).options(selectinload(Product.media)).where(Product.id == product_id)
    )
    if not item:
        raise HTTPException(status_code=404)
    return render(
        request, "admin/product_form.html", current_user=user, item=item, media=item.media
    )


# --- Médias -----------------------------------------------------------------


def _upload(db, background, file, *, service_id=None, product_id=None) -> str | None:
    """Valide, enregistre et planifie le traitement. Retourne un message d'erreur ou None."""
    try:
        staged = media_service.stage_upload(db, file)
    except media_service.UploadRejected as exc:
        return str(exc)
    media = media_service.create_media(db, staged, service_id=service_id, product_id=product_id)
    background.add_task(media_service.process_media, media.id, str(staged.path))
    return None


def _after_upload(request, error, kind, owner_id):
    base = f"/admin/{kind}/{owner_id}"
    if error:
        from urllib.parse import quote

        return _redirect(f"{base}?upload_error={quote(error)}")
    return _redirect(f"{base}?uploaded=1")


@router.post("/services/{service_id}/media")
def service_media_upload(
    request: Request,
    service_id: int,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not db.get(Service, service_id):
        raise HTTPException(status_code=404)
    error = _upload(db, background, file, service_id=service_id)
    return _after_upload(request, error, "services", service_id)


@router.post("/products/{product_id}/media")
def product_media_upload(
    request: Request,
    product_id: int,
    background: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not db.get(Product, product_id):
        raise HTTPException(status_code=404)
    error = _upload(db, background, file, product_id=product_id)
    return _after_upload(request, error, "products", product_id)


@router.post("/media/{media_id}/delete")
def media_delete(media_id: int, db: Session = Depends(get_db)):
    media = db.get(Media, media_id)
    if not media:
        raise HTTPException(status_code=404)
    back = (
        f"/admin/services/{media.service_id}"
        if media.service_id
        else f"/admin/products/{media.product_id}"
    )
    media_service.delete_media_files(media)
    db.delete(media)
    db.commit()
    return _redirect(back)
