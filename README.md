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
