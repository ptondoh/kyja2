"""Coordonnées du salon affichées sur le site (réglées par l'admin dans « Réglages »)."""
import time

from app.db.base import SessionLocal
from app.db.models.settings import get_setting

_TTL_S = 60
_cache: dict[str, tuple[float, str]] = {}


def salon_phone() -> str:
    """Téléphone du salon, ou chaîne vide s'il n'est pas renseigné. Mis en cache 60 s (appelé à chaque page)."""
    now = time.monotonic()
    hit = _cache.get("phone")
    if hit and now - hit[0] < _TTL_S:
        return hit[1]
    try:
        with SessionLocal() as db:
            value = get_setting(db, "salon_phone").strip()
    except Exception:  # la base indisponible ne doit pas casser l'affichage d'une page
        value = ""
    _cache["phone"] = (now, value)
    return value


def reset_cache() -> None:
    _cache.clear()
