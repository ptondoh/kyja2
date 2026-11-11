"""Envoi des messages : par Celery si `USE_CELERY=true` et que Redis répond, sinon dans le processus du site.

Le site ne dépend jamais de Redis pour répondre : si le courtier est injoignable, l'envoi se fait sur place.
"""
import logging
import threading

from app.config import get_settings

log = logging.getLogger("kyja.dispatch")


def celery_enabled() -> bool:
    return get_settings().use_celery


def send_messages(ids: list[int], *, threaded: bool = False) -> None:
    """Envoie les messages en file. `threaded=True` : appelé depuis une requête qui ne doit pas attendre le fournisseur."""
    if not ids:
        return
    if celery_enabled():
        try:
            from app.tasks.jobs import deliver_messages

            deliver_messages.delay(ids)
            return
        except Exception:  # noqa: BLE001 - Redis arrêté : on n'abandonne pas le message, on l'envoie ici
            log.warning("courtier Celery injoignable : envoi dans le processus du site", exc_info=True)
    from app.notifications import service

    if threaded:
        threading.Thread(target=service.deliver_in_background, args=(ids,), daemon=True).start()
    else:
        service.deliver_in_background(ids)
