"""Application Celery : tâches de fond (envoi des messages) et tâches planifiées (rappels, nettoyages).

Lancement (voir README / TODO.md) :
    celery -A app.tasks.celery_app worker -B -P solo -l info     # développement, Windows compris (un seul processus)
    celery -A app.tasks.celery_app worker -l info                # production : un worker …
    celery -A app.tasks.celery_app beat -l info                  # … et un seul « beat » (le planificateur)

Tant que `USE_CELERY` n'est pas à `true`, le site ne s'en sert pas : il garde son fonctionnement intégré
(envoi après la réponse, nettoyage dans le processus), donc rien ne casse sans Redis.
"""
from celery import Celery

from app.config import get_settings

settings = get_settings()

celery_app = Celery("kyja", broker=settings.celery_broker_url or settings.redis_url, include=["app.tasks.jobs"])
celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_serializer="json",
    accept_content=["json"],
    task_always_eager=settings.celery_always_eager,
    task_acks_late=True,  # une tâche interrompue est reprise plutôt que perdue
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    beat_schedule={
        "annuler-les-prix-sans-reponse": {"task": "kyja.expire_quotes", "schedule": 300.0},
        "rappels-de-rendez-vous": {"task": "kyja.send_reminders", "schedule": 600.0},
        "reprise-des-messages-en-file": {"task": "kyja.retry_stuck_messages", "schedule": 900.0},
        "nettoyage-des-brouillons": {"task": "kyja.purge_drafts", "schedule": 3600.0},
    },
)
