"""Nettoyage périodique : un prix resté sans réponse annule le rendez-vous et libère le créneau.

Tourne dans le processus du site (aucun Redis requis). Avec Celery (plus tard), `sweep_once` deviendra une tâche planifiée.
"""
import asyncio
import logging

from app.bookings import quotes
from app.db.base import SessionLocal
from app.notifications import messages
from app.notifications import service as notifications

log = logging.getLogger("kyja.sweeper")
INTERVAL_SECONDS = 300


def sweep_once() -> int:
    """Annule les propositions expirées et prévient la cliente (alerte du site) et l'équipe. Retourne le nombre annulé."""
    with SessionLocal() as db:
        expired = quotes.expire_stale_quotes(db)
        for appt in expired:
            title, body = messages.staff_quote_expired(appt)
            notifications.notify_staff(db, appt, kind="quote_expired", title=title, body=body)
            notifications.notify_user(
                db, appt.client, kind="quote_expired", title="Prix sans réponse : rendez-vous annulé",
                body=f"Le délai pour répondre est dépassé : votre rendez-vous « {appt.service.name} » est annulé. Vous pouvez réserver à nouveau.",
                url="/bookings/new", appointment=appt,
            )  # fmt: skip
        return len(expired)


async def run_forever() -> None:
    while True:
        try:
            await asyncio.to_thread(sweep_once)
        except Exception:  # noqa: BLE001 - une erreur passagère ne doit jamais arrêter la boucle
            log.exception("échec du nettoyage des prix expirés")
        await asyncio.sleep(INTERVAL_SECONDS)
