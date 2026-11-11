"""Tâches Celery. Chacune ouvre sa propre session et ne fait qu'appeler du code déjà testé ailleurs."""
import logging

from app.db.base import SessionLocal
from app.notifications import reminders, service, sweeper
from app.tasks.celery_app import celery_app

log = logging.getLogger("kyja.tasks")


@celery_app.task(name="kyja.deliver_messages", bind=True, max_retries=3)
def deliver_messages(self, ids: list[int]) -> None:
    """Envoie des courriels / SMS en file. Un échec réessaie avec un délai croissant (30 s, 2 min, 8 min)."""
    with SessionLocal() as db:
        failed = service.deliver(db, ids)
    if failed:
        raise self.retry(countdown=30 * 4**self.request.retries, kwargs={"ids": failed})


@celery_app.task(name="kyja.expire_quotes")
def expire_quotes() -> int:
    return sweeper.sweep_once()


@celery_app.task(name="kyja.send_reminders")
def send_reminders() -> dict:
    with SessionLocal() as db:
        return reminders.send_due_reminders(db)


@celery_app.task(name="kyja.retry_stuck_messages")
def retry_stuck_messages() -> int:
    """Reprend les messages restés « en file » (processus arrêté en plein envoi) ou en échec récent."""
    with SessionLocal() as db:
        return service.retry_stuck(db)


@celery_app.task(name="kyja.purge_drafts")
def purge_drafts() -> dict:
    from app.bookings import drafts, photos

    with SessionLocal() as db:
        return {"drafts": drafts.purge_expired(db), "photos": photos.purge_stale(db)}
