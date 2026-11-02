# KYJA BEAUTÉ

Application web d'un salon de coiffure et de produits de beauté : catalogue (photos et vidéos),
**réservation en ligne** avec durée calculée selon la coiffeuse, espace client conforme à la
loi canadienne sur la protection des renseignements personnels (LPRPDE) et administration du salon.

> **Version v02.** Le MVP du cahier des charges est fonctionnel. Il reste les emails et la mise en
> production (voir [État du projet](#état-du-projet)).
> Besoin du salon : [`PRD.md`](PRD.md) · Conception technique : [`ARCHITECTURE.md`](ARCHITECTURE.md)

---

## Ce que fait l'application

### Pour les clientes
- **Catalogue** des prestations et des produits, avec galeries de **photos et de vidéos**
  (vidéos de 3 minutes et 100 Mo au maximum, réglables par l'administration).
- **Réservation en ligne, sans compte jusqu'à la confirmation.** Deux façons de réserver :
  1. **Choisir une coiffure** du catalogue, avec un **prix estimatif** (le montant final est fixé au salon) ;
  2. **Demande sur mesure** : la cliente décrit la coiffure souhaitée et joint **jusqu'à 10 photos**
     d'inspiration (aperçu immédiat). Le prix est « à confirmer au salon ».
- **Durée calculée** : `durée de base × coefficient de la coiffeuse + temps de repos`, arrondie au pas
  du calendrier (15 min). Elle est affichée **par coiffeuse** (« 2 h 15 avec Sonia, 3 h avec Marie »).
- **Calendrier mensuel** : seuls les jours où la durée complète tient sont cliquables ; un clic affiche
  les heures précises. Option « peu importe » : la première coiffeuse disponible est assignée.
- **Compte demandé au dernier moment** : le choix, la description et les photos sont conservés
  pendant l'inscription ou la connexion, puis la cliente revient directement au récapitulatif.
- **Mes rendez-vous** : historique et annulation. Paiement au salon (argent comptant ou Interac).
- **Mon profil** : informations, consentement marketing (jamais coché par défaut), changement de mot
  de passe, **téléchargement de toutes ses données**, et **droit à l'oubli** (anonymisation).
- Session qui se **renouvelle automatiquement** : plus de déconnexion toutes les 15 minutes.

### Pour le salon (espace administrateur, `/admin`)
- **Prestations et produits** : prix, durées, catégories, photos et vidéos (compressées automatiquement).
- **Coiffeuses** : coefficient de durée et horaires hebdomadaires. **Équipements** (bacs de lavage,
  casques) : une prestation qui en requiert un n'est réservable que s'il en reste un de libre.
- **Rendez-vous** : liste, annulation, **saisie du prix final** au salon, et **création d'un rendez-vous
  par téléphone** pour une cliente sans compte.
- **Réglages** : pas du calendrier, horizon et délai minimal de réservation, téléphone du salon,
  limites des vidéos.

### Sécurité et vie privée
- Mots de passe hachés en **Argon2id** ; cookies `HttpOnly` et `SameSite=Strict` ; protection **CSRF** ;
  politique de sécurité du contenu (**CSP**) avec nonce ; limitation des tentatives de connexion.
- **Aucun conflit de réservation** possible (verrous en base) : testé avec des réservations simultanées.
  Prix, durée et coefficient sont **recalculés par le serveur** : un formulaire falsifié n'a aucun effet.
- **Photos des clientes privées** : jamais d'adresse publique ; lisibles seulement par la cliente,
  l'administration et la coiffeuse assignée ; métadonnées (dont la géolocalisation) supprimées.
- **Droit à l'oubli** : identité, photos et descriptions effacées ; seuls l'historique comptable (montants,
  7 ans) et les preuves de consentement sont conservés, sans identifier la personne.

---

## État du projet

| | |
|---|---|
| **Fait (MVP)** | Catalogue multimédia · réservation (2 modes, durée calculée, calendrier, équipements) · comptes et sessions · profil, export et droit à l'oubli · administration · design aux couleurs du logo · 228 tests automatiques |
| **Pas encore fait** | **Emails** de confirmation et de rappel (nécessite Redis, Celery et un compte Resend) · **mise en production** (serveur, HTTPS, sauvegardes) · chiffrement du téléphone et de l'adresse dans la base |
| **Prévu ensuite** (cahier des charges, phases 2 et 3) | Portail des coiffeuses indépendantes · commissions et finance · livraison à domicile · fidélité · vente au comptoir · application mobile |
| **Limites connues** | La limitation des tentatives de connexion est gardée en mémoire (un seul processus) · le stockage Cloudflare R2 est écrit mais n'a pas été essayé avec un vrai compte · testé avec Edge sous Windows seulement |

---

## Technologies

Python 3.12+ · **FastAPI** · pages rendues par le serveur (**Jinja2**) avec **HTMX** · **Tailwind CSS**
(binaire autonome, sans Node.js) · **PostgreSQL** · SQLAlchemy 2 et Alembic · Argon2id · Pillow et
ffmpeg (images et vidéos) · pytest et **Playwright** (tests dans un vrai navigateur).

---

## Installation

**Prérequis :** Python 3.12 ou plus, PostgreSQL 16 ou 17 en marche. Redis n'est pas nécessaire pour l'instant.
Les commandes ci-dessous sont pour **Windows (PowerShell)** ; sous macOS/Linux, remplacez
`.venv\Scripts\python` par `.venv/bin/python`.

```powershell
# 1. Environnement Python et dépendances
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"

# 2. Configuration (puis ouvrir .env et remplacer SECRET_KEY par une vraie clé)
copy .env.example .env
.venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(48))"

# 3. Bases de données : une pour l'application, une pour les tests (mots de passe de DÉVELOPPEMENT seulement)
psql -U postgres -c "CREATE USER kyja WITH PASSWORD 'kyja';" -c "CREATE DATABASE kyja OWNER kyja;" -c "CREATE DATABASE kyja_test OWNER kyja;"

# 4. Création des tables
.venv\Scripts\alembic upgrade head
```

Le CSS est déjà compilé et versionné. Pour modifier les styles, téléchargez le binaire autonome
[Tailwind v4.3.3](https://github.com/tailwindlabs/tailwindcss/releases/tag/v4.3.3) dans `bin\tailwindcss.exe`
(vérifiez son SHA-256 avec le fichier publié), puis :

```powershell
bin\tailwindcss.exe -i app/static/css/input.css -o app/static/css/style.css --minify
```

---

## Essayer l'application

### Lancer le site avec des données d'exemple

```powershell
.venv\Scripts\python -m scripts.seed_demo                                   # 2 coiffeuses, 5 prestations, 2 bacs, 2 produits
.venv\Scripts\python -m scripts.create_admin votre@email.com "Votre Nom"    # votre compte administrateur (mot de passe demandé)
.venv\Scripts\python -m uvicorn app.main:app --reload                       # puis ouvrir http://localhost:8000
```

Arrêt : `Ctrl+C`. Retrait des données d'exemple : `.venv\Scripts\python -m scripts.seed_demo --remove`
(il ne retire que ce qu'il a créé).

### Parcours à essayer

**En visiteuse** (fenêtre de navigation privée) :
1. « Réserver » → « Choisir une coiffure » → une prestation. Changez de coiffeuse : les durées et les jours
   disponibles changent.
2. Choisissez un jour dans le calendrier, puis une heure : le récapitulatif s'affiche. À la « dernière
   étape », créez un compte : vous revenez directement au récapitulatif. Confirmez.
3. « Mes rendez-vous » : retrouvez-le et annulez-le.
4. « Réserver » → « Demande sur mesure » : décrivez la coiffure et ajoutez des photos (les miniatures
   apparaissent aussitôt, 10 maximum).

**Dans votre compte** (cliquez votre nom en haut à droite → « Mon profil ») : modifiez vos informations et
le consentement marketing, téléchargez vos données, changez le mot de passe, puis essayez « Supprimer mon
compte » **avec un compte de test** (le message liste ce qui est effacé et ce qui est conservé).

**En administrateur** (menu « Admin ») :
- *Prestations* : ajoutez une photo et une vidéo, puis regardez le résultat sur le site.
- *Rendez-vous* : saisissez le prix final d'un rendez-vous ; créez-en un « par téléphone ».
- *Coiffeuses* : changez un coefficient ou des horaires et revoyez le calendrier.
- *Réglages* : renseignez le téléphone du salon (il s'affiche en bas de page).

**La session :** une fois connectée, supprimez le cookie `access_token` (F12 → Application → Cookies) puis
rechargez la page : vous restez connectée.

**Sur mobile :** mode appareil des outils du navigateur (`F12`, icône téléphone).
Pour votre vrai téléphone sur le même Wi-Fi : lancer avec `--host 0.0.0.0`, ajouter l'adresse de votre
ordinateur dans `ALLOWED_HOSTS` du fichier `.env` (ex. `ALLOWED_HOSTS=["localhost","192.168.1.20"]`) et
autoriser le port 8000 dans le pare-feu.

---

## Tests automatiques

Les tests utilisent leur **propre base `kyja_test`** et leurs propres dossiers de fichiers : ils ne touchent
jamais à vos données de développement. Le schéma de test se met à jour tout seul. Les tests refusent de
démarrer si la base visée n'est pas une base `*_test`.

```powershell
.venv\Scripts\python -m pytest                      # tout : 228 tests, environ 3 à 4 minutes
.venv\Scripts\python -m pytest --ignore=tests/e2e   # sans navigateur : environ 40 secondes
.venv\Scripts\python -m pytest tests/e2e            # seulement les parcours dans un vrai navigateur
.venv\Scripts\python scripts/security_check.py      # analyse du code (bandit) et des dépendances (pip-audit)
```

Les tests de navigateur (Playwright) utilisent **Microsoft Edge** (présent sous Windows). Variables utiles :

| Variable | Effet |
|---|---|
| `E2E_HEADED=1` | affiche la fenêtre du navigateur pendant les tests |
| `E2E_SLOWMO=600` | ralentit chaque action (millisecondes) pour bien suivre |
| `E2E_BROWSER=chromium` | utilise Chromium de Playwright (`playwright install chromium`) ; `chrome` pour Chrome. *Seul Edge a été vérifié.* |
| `TEST_DATABASE_URL` | autre base de test (son nom doit finir par `_test`) |

Exemple pour regarder un parcours se dérouler :
`$env:E2E_HEADED="1"; $env:E2E_SLOWMO="600"; .venv\Scripts\python -m pytest tests/e2e/test_booking_flow.py -s`

Les tests couvrent notamment : le calcul des durées, les conflits (y compris des réservations simultanées),
les droits d'accès, la falsification de formulaires, le renouvellement de session, le droit à l'oubli et les
parcours complets dans un navigateur à plusieurs largeurs d'écran. Les captures prises par les tests sont
dans `tmp_uploads/shots/`.

---

## Configuration (fichier `.env`)

| Variable | Rôle | Défaut |
|---|---|---|
| `APP_ENV` | `dev` ou autre (en production : clé obligatoire, documentation de l'API masquée, HSTS) | `dev` |
| `SECRET_KEY` | clé de signature (32 caractères minimum hors développement) | `change-me` |
| `DATABASE_URL` | base PostgreSQL | `postgresql+psycopg://kyja:kyja@localhost:5432/kyja` |
| `ALLOWED_HOSTS` | noms de domaine acceptés (liste JSON) | `["localhost","127.0.0.1","testserver"]` |
| `SALON_TIMEZONE` | fuseau horaire des horaires du salon | `America/Toronto` |
| `MEDIA_DIR`, `PRIVATE_DIR` | dossiers des médias publics et des photos privées (développement) | `./media_files`, `./private_files` |
| `IMAGE_MAX_MB` | taille maximale d'une image | `10` |
| `FFMPEG_BIN` | chemin de ffmpeg (sinon celui du système, sinon celui fourni par `[dev]`) | vide |
| `R2_*`, `R2_PRIVATE_BUCKET` | stockage Cloudflare R2 en production (public et privé) | vide |
| `RESEND_API_KEY`, `EMAIL_FROM` | envoi d'emails (pas encore utilisé) | vide |
| `REDIS_URL` | Redis (pas encore utilisé) | `redis://localhost:6379/0` |

Les réglages du salon (calendrier, vidéos, téléphone) se modifient dans **Admin → Réglages**, sans toucher au fichier.

---

## Structure du projet

```
app/
  auth/        connexion, sessions, renouvellement silencieux, CSRF
  bookings/    moteur de réservation, calendrier, brouillons, photos d'inspiration
  clients/     profil, export des données, droit à l'oubli
  admin/       administration (catalogue, coiffeuses, équipements, rendez-vous, réglages)
  services/ products/ media/   catalogue public, médias (images, vidéos, stockage)
  db/models/   tables SQLAlchemy
  templates/ static/   pages (Jinja2), CSS compilé, polices, HTMX, logo
alembic/       migrations de la base
scripts/       seed_demo, create_admin, security_check, make_logo
tests/         tests unitaires, HTTP et navigateur (tests/e2e)
design/        logo d'origine
```

Autres commandes : `.venv\Scripts\python scripts/make_logo.py` régénère le logo détouré à partir de
`design/logo-source.png` ; `.venv\Scripts\alembic revision --autogenerate -m "description"` crée une migration.

---

## Branches

- `master` : développement en cours.
- `v01` : version figée après le catalogue et la réservation de base.
- `v02` : cette version (MVP fonctionnel avec profil client et droit à l'oubli), avec ce README.
