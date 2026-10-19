import secrets

from fastapi import HTTPException, Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.security import csrf_token_valid, new_csrf_token
from app.config import get_settings

CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "x-csrf-token"
CSRF_FORM_FIELD = "csrf_token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def build_csp(nonce: str) -> str:
    s = get_settings()
    media = f"'self' {s.r2_public_base_url}".strip()
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src 'self' 'nonce-{nonce}'",
            "style-src 'self'",
            f"img-src {media} data: blob:",
            f"media-src {media}",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


class SecurityMiddleware(BaseHTTPMiddleware):
    """En-têtes de sécurité (CSP avec nonce, HSTS…) + cookie CSRF."""

    async def dispatch(self, request: Request, call_next):
        s = get_settings()
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce

        csrf_cookie = request.cookies.get(CSRF_COOKIE)
        fresh_csrf = None
        if not csrf_token_valid(csrf_cookie):
            fresh_csrf = new_csrf_token()
            csrf_cookie = fresh_csrf
        request.state.csrf_token = csrf_cookie

        response = await call_next(request)

        if fresh_csrf:
            response.set_cookie(
                CSRF_COOKIE,
                fresh_csrf,
                httponly=True,
                secure=not s.is_dev,
                samesite="strict",
                path="/",
            )
        response.headers["Content-Security-Policy"] = build_csp(nonce)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if not s.is_dev:
            response.headers["Strict-Transport-Security"] = (
                "max-age=63072000; includeSubDomains; preload"
            )
        return response


async def verify_csrf(request: Request) -> None:
    """Dépendance FastAPI : double-submit (cookie == en-tête ou champ de formulaire)."""
    if request.method in SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    submitted = request.headers.get(CSRF_HEADER)
    if not submitted:
        form = await request.form()
        submitted = form.get(CSRF_FORM_FIELD)
    if (
        not cookie
        or not submitted
        or not csrf_token_valid(cookie)
        or not secrets.compare_digest(str(submitted), cookie)
    ):
        raise HTTPException(status_code=403, detail="Jeton CSRF invalide")
