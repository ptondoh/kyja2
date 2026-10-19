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

`ffmpeg` doit être installé pour le traitement des vidéos.
