from pathlib import Path

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.orm import Session, selectinload
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.admin.router import router as admin_router
from app.auth.deps import get_current_user_optional
from app.auth.router import router as auth_router
from app.config import get_settings
from app.db.base import get_db
from app.db.models.core import Product, Service, User
from app.products.router import router as products_router
from app.services.router import ready_media
from app.services.router import router as services_router
from app.security_middleware import SecurityMiddleware
from app.templating import render

settings = get_settings()
if not settings.is_dev and (settings.secret_key == "change-me" or len(settings.secret_key) < 32):  # nosec B105
    raise RuntimeError("SECRET_KEY doit être défini (32 caractères minimum) hors développement")

# Pas de documentation OpenAPI publique en production.
app = FastAPI(
    title="KYJA BEAUTÉ",
    docs_url="/docs" if settings.is_dev else None,
    redoc_url=None,
    openapi_url="/openapi.json" if settings.is_dev else None,
)
app.add_middleware(SecurityMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")
Path(settings.media_dir).mkdir(parents=True, exist_ok=True)
app.mount("/media-files", StaticFiles(directory=settings.media_dir), name="media")
app.include_router(auth_router)
app.include_router(services_router)
app.include_router(products_router)
app.include_router(admin_router)


@app.get("/")
def index(
    request: Request,
    db: Session = Depends(get_db),
    user: User | None = Depends(get_current_user_optional),
):
    services = db.scalars(
        select(Service)
        .options(selectinload(Service.media))
        .where(Service.is_active.is_(True))
        .order_by(Service.id.desc())
        .limit(4)
    ).all()
    products = db.scalars(
        select(Product)
        .options(selectinload(Product.media))
        .where(Product.is_active.is_(True))
        .order_by(Product.id.desc())
        .limit(4)
    ).all()
    return render(
        request,
        "index.html",
        current_user=user,
        services=[(s, ready_media(s.media)) for s in services],
        products=[(p, ready_media(p.media)) for p in products],
    )


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "error", "db": "down"}, status_code=503)
    return {"status": "ok"}
