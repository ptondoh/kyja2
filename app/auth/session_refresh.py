"""Renouvellement silencieux de la session.

Le cookie d'accès dure 15 minutes ; le cookie de renouvellement, 30 jours. Sans ce middleware, la
cliente était déconnectée au bout de 15 minutes. Ici, quand le cookie d'accès manque ou a expiré mais
que le renouvellement est valide, un nouveau cookie d'accès est émis AVANT que la requête n'atteigne la
route (elle voit donc la cliente connectée), et posé sur la réponse. Le jeton de renouvellement est
remplacé à chaque fois (rotation) ; son rejeu bien après la rotation coupe toutes les sessions (vol).
"""
from http.cookies import SimpleCookie

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.auth import service
from app.auth.deps import ACCESS_COOKIE, REFRESH_COOKIE
from app.auth.security import decode_access_token
from app.db.base import SessionLocal

# Pas de renouvellement pour les fichiers statiques (aucune session utile, et inutile de solliciter la
# base) ni pour la déconnexion (le renouvellement ne doit jamais défaire une déconnexion en cours).
SKIP_PREFIXES = ("/static/", "/media-files/", "/health", "/favicon")
SKIP_PATHS = {"/auth/logout", "/auth/logout-all", "/auth/refresh"}


def _inject_cookies(scope: dict, updates: dict[str, str]) -> None:
    """Fait voir à la route (et à ses dépendances) les cookies tout juste renouvelés."""
    jar = SimpleCookie()
    headers = []
    for name, value in scope["headers"]:
        if name == b"cookie":
            jar.load(value.decode("latin-1"))
        else:
            headers.append((name, value))
    for key, value in updates.items():
        jar[key] = value
    headers.append((b"cookie", "; ".join(f"{k}={m.value}" for k, m in jar.items()).encode("latin-1")))
    scope["headers"] = headers


def _refresh(raw: str):
    with SessionLocal() as db:
        return service.silent_refresh(db, raw)


class SessionRefreshMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        raw = request.cookies.get(REFRESH_COOKIE)
        access = request.cookies.get(ACCESS_COOKIE)
        if (
            not raw
            or path.startswith(SKIP_PREFIXES)
            or path in SKIP_PATHS
            or (access and decode_access_token(access))  # session encore valide : rien à faire
        ):
            return await call_next(request)

        result = await run_in_threadpool(_refresh, raw)
        if result is None:
            # Jeton inconnu, expiré ou rejoué : on l'efface pour ne plus le présenter à chaque requête.
            response = await call_next(request)
            response.delete_cookie(REFRESH_COOKIE, path="/")
            return response

        user, new_refresh = result
        cookies = service.session_cookies(user, new_refresh)
        _inject_cookies(request.scope, {key: value for key, value, _ in cookies})  # la route voit la session
        response = await call_next(request)
        for key, value, options in cookies:  # et le navigateur reçoit les nouveaux cookies
            response.set_cookie(key, value, **options)
        return response
