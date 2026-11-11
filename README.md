# KYJA BEAUTÉ

Application web du salon (réservation, catalogue images/vidéos, RH, finance, livraison, fidélité).
Voir `PRD.md` (besoins) et `ARCHITECTURE.md` (technique).

## Démarrage (dev)

```
cp .env.example .env
# PostgreSQL et Redis doivent être installés et lancés en local
python -m venv .venv && .venv/Scripts/activate
pip install -e ".[dev]"
alembic upgrade head
uvicorn app.main:app --reload
```

Pour voir le site avec des données (coiffeuses, horaires, prestations) : `python -m scripts.seed_demo`
(retrait : `--remove`). Premier compte admin : `python -m scripts.create_admin email "Nom"`.

`ffmpeg` doit être installé pour le traitement des vidéos.

## Tâches de fond (Redis + Celery)

Optionnel en développement : sans `USE_CELERY=true`, le site envoie les messages et nettoie tout seul, dans son processus.
Avec Celery, les envois sont mis en file (reprise automatique en cas d'échec) et un planificateur lance :
rappels de rendez-vous (la veille et 2 h avant), annulation des prix sans réponse, reprise des messages restés en file,
nettoyage des brouillons.

1. Démarrer Redis (sur Linux : `sudo apt install redis-server` ; sur Windows : Memurai ou Redis dans WSL — pas de Docker).
2. Dans `.env` : `USE_CELERY=true` (et `REDIS_URL` si Redis n'est pas sur `localhost:6379`).
3. Lancer le worker et le planificateur (en développement, un seul processus suffit, y compris sous Windows) :

```
celery -A app.tasks.celery_app worker -B -P solo -l info
```

En production : un `worker` (`celery -A app.tasks.celery_app worker -l info`) et **un seul** `beat` (`celery -A app.tasks.celery_app beat -l info`).
Si Redis tombe, le site continue de répondre et envoie alors les messages lui-même.

**Essayer les rappels sans attendre la veille** (depuis le dossier du projet, avec le Python du projet) :

```
.\.venv\Scripts\python.exe -m scripts.simulate_reminders                    # liste les rendez-vous confirmés
.\.venv\Scripts\python.exe -m scripts.simulate_reminders 12 --avant 20h     # simule le rappel de la veille
.\.venv\Scripts\python.exe -m scripts.simulate_reminders 12 --avant 1h50    # rappel 2 h avant
.\.venv\Scripts\python.exe -m scripts.simulate_reminders 12 --reset         # recommencer
```

Les courriels et SMS restent en « aperçu » (rien ne part) tant que Resend et Twilio ne sont pas configurés ; ils se lisent dans **Admin → Messages envoyés**.

## Design (Tailwind, sans Node)

Le binaire autonome Tailwind v4.3.3 se place dans `bin/tailwindcss.exe` (non versionné, voir
https://github.com/tailwindlabs/tailwindcss/releases/tag/v4.3.3 et vérifier le SHA-256 publié).

```
bin\tailwindcss.exe -i app/static/css/input.css -o app/static/css/style.css --watch      # dev
bin\tailwindcss.exe -i app/static/css/input.css -o app/static/css/style.css --minify    # build
```

`style.css` (généré) est versionné pour que le site fonctionne sans le binaire.
Le logo détouré se régénère avec `python scripts/make_logo.py` (source : `design/logo-source.png`).
Polices (Cormorant Garamond, Jost) hébergées dans `app/static/fonts/` : la CSP interdit les CDN.

## Tests

Les tests tournent sur une base **séparée `kyja_test`** (créée une fois : `CREATE DATABASE kyja_test OWNER kyja;`),
avec leurs propres dossiers de fichiers : ils ne touchent jamais à vos données de développement.
Le schéma de test se met à jour tout seul (migrations Alembic). `TEST_DATABASE_URL` permet d'en choisir une autre
(son nom doit finir par `_test`).

```
pytest                 # unitaires + intégration + e2e (Playwright, Edge)
python scripts/security_check.py   # bandit + pip-audit
```
