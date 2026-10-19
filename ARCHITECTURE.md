# Architecture Technique — KYJA BEAUTÉ

## Vue d'ensemble

Application de gestion de salon de coiffure multi-phases (MVP → V1 → V2), avec moteur de réservation à ressources multiples, module RH pour coiffeuses indépendantes, finance, logistique de livraison, inventaire hybride et programme de fidélité.

Contrairement à une architecture classique "SPA découplée + API", le frontend web est **rendu côté serveur par FastAPI** (Jinja2) et enrichi avec HTMX/Alpine.js. Objectif : éliminer la dépendance à Node.js/npm en production, réduire le nombre de services déployés, et éviter l'instabilité des versions bleeding-edge d'un framework JS (Next.js/React).

---

## Stack Technique

| Couche | Technologie | Rôle |
|---|---|---|
| **Rendu HTML** | Jinja2 (intégré à FastAPI) | Templates serveur, pages catalogue/réservation/profil/admin |
| **Interactivité partielle** | HTMX | Mises à jour partielles de page sans reload (grille de disponibilités, validation admin, polling) |
| **Réactivité locale** | Alpine.js | État UI léger (wizard de réservation, popups, toggles) sans build step |
| **CSS** | TailwindCSS (CLI standalone, sans npm/Node) | Design system, responsive |
| **Backend** | FastAPI (Python 3.12) | API REST (future app mobile), rendu de templates, logique métier, calculs |
| **Base de données** | PostgreSQL | Données transactionnelles et relationnelles |
| **ORM** | SQLAlchemy 2.0 + Alembic | Accès BDD, migrations |
| **Cache / Broker** | Redis | Disponibilités, sessions, rate limiting, broker Celery |
| **Tâches async** | Celery + Celery Beat | Rappels RDV, envois email, traitement images et vidéos (Pillow, ffmpeg) |
| **Stockage fichiers** | Cloudflare R2 + CDN | Images et vidéos galerie/catalogue, preuves de livraison, portfolios |
| **Auth web** | Sessions JWT en cookie HttpOnly + Argon2id | Authentification sécurisée, protégée des vols via JS (XSS) |
| **Auth future mobile** | JWT Bearer (même émetteur) + refresh tokens | Réutilisable sans changement backend pour React Native (Phase 3+) |
| **Email** | Resend | Confirmations RDV, statuts livraison |
| **Push notifications** | Web Push API (service worker écrit à la main) | Alertes temps-réel clientes |
| **Hébergement** | VPS unique (Hostinger ou OVH) | Backend + rendu frontend + BDD + Redis + Celery — **un seul service à déployer** |

> **Changement clé vs version précédente :** plus de projet Next.js séparé, plus de déploiement Vercel. Tout tourne sur le même VPS. Économie estimée : ~27 $CAD/mois (plan Vercel Pro) + simplification du pipeline de déploiement (un seul `git pull` + un seul service à redémarrer).

---

## Infrastructure

### Hébergement VPS (unique)

```
VPS (Hostinger / OVH)
├── FastAPI          → Uvicorn + Gunicorn (port 8000)
│   ├── Routes HTML  → Jinja2 (web : clientes, coiffeuses, admin)
│   └── Routes JSON  → API REST (futur app mobile React Native)
├── Fichiers statiques → CSS compilé (Tailwind), htmx.js, alpine.js, service worker PWA
├── Celery Worker    → Tâches asynchrones (rappels, emails, traitement images, transcodage vidéo via ffmpeg)
├── Celery Beat      → Planificateur cron (rappels J-1 et H-2)
├── PostgreSQL       → Option managée du fournisseur VPS
├── Redis            → Cache + Broker Celery + sessions
└── Nginx            → Reverse proxy + servir les fichiers statiques + SSL/TLS (Certbot/Let's Encrypt)
```

> **Note PIPEDA :** Choisir un VPS avec datacenter au Canada si possible (OVH Beauharnois QC, ou Hostinger région US-Est la plus proche). Documenter la localisation des données dans la politique de confidentialité.

> **RAM minimale recommandée :** 2 Go pour faire tourner FastAPI + Celery + Redis + PostgreSQL simultanément. Le rendu Jinja2/HTMX n'ajoute pas de charge significative (pas de build Node à exécuter sur le serveur).

### Build CSS (Tailwind standalone)

```
Dev   : tailwindcss-cli --watch -i input.css -o static/css/style.css
Build : tailwindcss-cli -i input.css -o static/css/style.css --minify
```

Aucune installation de Node.js/npm requise, ni en dev ni en production — le binaire Tailwind standalone est téléchargé une fois et versionné/documenté dans le repo.

### Backups PostgreSQL

```
Backups automatisés
├── pg_dump quotidien via cron système
├── Rétention : 30 jours
├── Stockage : bucket R2 séparé (kyja-backups)
└── Test de restauration : trimestriel (procédure documentée)
```

### Stockage des fichiers (Cloudflare R2)

```
Upload (FastAPI) → R2 Bucket (kyja-beaute)
                        ├── /gallery/images/       → Photos coiffures & produits (publiques)
                        ├── /gallery/videos/       → Courtes vidéos coiffures & produits (publiques)
                        ├── /products/images/      → Images catalogue (publiques)
                        ├── /products/videos/      → Vidéos catalogue (publiques)
                        ├── /deliveries/           → Preuves de livraison (accès admin)
                        ├── /portfolios/pending/   → Upload initial coiffeuse (privé)
                        └── /portfolios/approved/  → Validé par admin, visible aux clientes

Accès client → CDN Cloudflare → cache géographique → média servi en < 100ms (image) / lecture en streaming (vidéo)
```

> **Catalogue multimédia (prestations & produits) :** chaque fiche service/produit peut avoir plusieurs images **et** courtes vidéos (démonstration, avant/après — cf. `PRD.md`, Phase 1 « Catalogue » et exigences de performance). Contraintes MVP : vidéos courtes uniquement (**3 min max**, taille plafonnée à l'upload : 100 Mo par défaut, valeur configurable par l'admin), pas de streaming adaptatif (pas de Cloudflare Stream en Phase 1) — R2 + CDN suffit pour des clips de 3 min max, lus directement via la balise `<video>` HTML dans la page Jinja2.

**Cycle de vie du portfolio coiffeuse :**
```
Coiffeuse uploade → /portfolios/pending/ (privé)
       ↓ Validation admin
/portfolios/approved/ → visible aux clientes pour choisir leur coiffeuse
```

**Pipeline d'upload (asynchrone) :**
```
1. Client envoie fichier → FastAPI (formulaire HTML classique ou HTMX)
2. FastAPI valide (type MIME réel, taille max, durée max pour vidéo : 180 s, rejet sinon) → retourne 202 Accepted (fragment HTML de confirmation)
3. Fichier stocké temporairement sur disque VPS
4. Tâche Celery selon le type de média :
     - image → Pillow : redimensionnement + conversion WebP
     - vidéo → ffmpeg : transcodage MP4 (H.264, `+faststart` pour lecture progressive),
               limite de résolution (720p), génération d'une miniature (poster) JPEG/WebP
5. Tâche Celery : upload vers R2 via boto3 (média + poster)
6. URLs et métadonnées (type, durée, taille, poster) sauvegardées en base, statut passe à `ready`
```

> **Vidéos :** le transcodage ffmpeg est CPU-intensif → file Celery dédiée (`media`) avec concurrence limitée à 1 pour ne pas ralentir le moteur de réservation sur le VPS. Tant que le statut est `processing`, le média n'est pas affiché dans le catalogue public. Le front utilise `<video preload="none" poster="...">` pour ne rien charger avant le clic.

---

## Architecture Applicative

### Vue globale des couches

```
┌───────────────────────────────────────────────┐
│                  Navigateur                    │
│   HTML (Jinja2) + HTMX + Alpine.js + Tailwind  │  ← Pas de build côté client, pas de SPA
│   Service Worker (PWA, Web Push)               │
└──────────────────────┬──────────────────────────┘
                       │ HTTP (pages + fragments HTML)
┌──────────────────────▼──────────────────────────┐
│                   FastAPI                       │
│   Routes HTML (Jinja2)  │  Routes JSON (API)    │  ← Backend + Frontend (VPS unique)
│   Pydantic (validation)                         │
│   SQLAlchemy ORM                                │
│   Auth middleware (cookie session + JWT bearer) │
└──────┬──────────────┬───────────────────────────┘
       │              │
┌──────▼──────┐  ┌────▼─────────────────────┐
│ PostgreSQL  │  │  Redis                   │
│ (données    │  │  ├── Cache disponibilités │
│  métier)    │  │  ├── Sessions            │
└─────────────┘  │  ├── Rate limiting       │
                 │  └── Broker Celery       │
                 └────────────┬─────────────┘
                              │
                 ┌────────────▼─────────────┐
                 │  Celery Worker + Beat    │
                 │  (rappels, emails,       │
                 │   images + vidéos)       │
                 └──────────────────────────┘
```

### Modules Backend (FastAPI)

Chaque module expose à la fois des **routes HTML** (pour le site web rendu serveur) et des **routes JSON** (pour l'admin si besoin d'AJAX fin, et pour la future app mobile React Native) :

```
app/
├── auth/           → Inscription, login, refresh token, logout, révocation
├── clients/        → Profil, historique, droit à l'oubli (PIPEDA)
├── bookings/        → Moteur de réservation, détection conflits ressources
├── services/       → Catalogue prestations + durées, médias (images/vidéos)
├── media/          → Upload, validation, métadonnées et statut de traitement des images/vidéos
├── hairdressers/   → Profils, disponibilités, coefficients, portfolio
├── resources/      → Bacs de lavage, casques (équipements partagés)
├── products/       → Catalogue produits, images/vidéos, prix
├── inventory/      → Stocks, alertes POS, synchronisation
├── orders/         → Commandes, livraisons, preuves photo
├── finance/        → Commissions, loyers de chaise, réconciliation
├── loyalty/        → Points, récompenses, historique
├── notifications/  → Email (Resend) + Push (Web Push)
├── tasks/          → Tâches Celery (rappels RDV, envois email, resize images, transcodage vidéo)
├── admin/          → Validation portfolio, validation paiements, POS
├── templates/      → Templates Jinja2 (pages + fragments HTMX)
└── static/         → CSS compilé (Tailwind), htmx.js, alpine.js, manifest PWA, service worker
```

### Pattern HTMX pour les interactions clés

```
Réservation (wizard en étapes, deux modes — voir PRD 4.A) :
  1. GET /bookings/new?mode=catalog|custom → page complète ("choisir une coiffure" ; mode custom : directement le formulaire)
  2. Choix coiffure → HTMX GET /bookings/hairdressers?service_id=X
                                            → fragment : coiffeuses avec LEUR durée calculée
                                              ("2 h 15 avec Marie") + option "peu importe"
  3. Choix coiffeuse → HTMX GET /bookings/calendar (jours disponibles du mois) ; clic sur un jour → HTMX GET /bookings/slots
                                            → fragment HTML (heures où la durée COMPLÈTE tient)
  4. Clic créneau → Alpine stocke la sélection en état local (pas de requête serveur)
  5. Récapitulatif (prix estimatif ou "à confirmer", durée, fin prévue)
  6. Soumission finale → HTMX POST /bookings/
                                            → recalcul serveur + transaction atomique + fragment de confirmation

  Le serveur ne fait jamais confiance aux valeurs du formulaire : prix, durée et coefficient sont recalculés
  côté serveur à l'étape 6.

Dashboard admin (validation portfolio) :
  Clic "Valider" → HTMX POST /admin/portfolios/{id}/approve
                → la ligne du tableau se remplace par son état "validé" (hx-swap="outerHTML")

POS "Vente Comptoir" :
  Alpine x-data gère la popup de rappel inventaire (setTimeout 10s), pas de round-trip serveur
```

---

## Base de Données — Schéma Principal

### Entités et relations clés

```
clients
  ├── appointments (1→N)
  ├── orders (1→N)
  ├── loyalty_account (1→1)
  └── consent_records (1→N)           ← PIPEDA

appointments
  ├── hairdresser (N→1)
  ├── service (N→1)
  ├── booking_mode (catalog | custom)
  ├── client_notes (mode custom) et photos (booking_photos, 0..10)
  ├── calcul mémorisé : base_duration_min, coefficient_used, buffer_min, computed_duration_min
  ├── estimated_price (copie du prix du catalogue, NULL si "à confirmer")
  ├── final_price (saisi au salon, NULL tant que la prestation n'est pas terminée)
  ├── resources (N→N via appointment_resources)   ← détection conflits
  └── financial_record (1→1)                       ← basé sur final_price

booking_photos                        ← photos d'inspiration, mode custom, DONNÉES PERSONNELLES
  ├── client_id (→ users, ON DELETE CASCADE)
  ├── appointment_id (→ appointments, ON DELETE CASCADE ; NULL = brouillon purgé après 24 h)
  └── storage_key, width, height, size_bytes

hairdressers
  ├── availability_slots (1→N)
  ├── portfolio_files (1→N)           ← pending ou approved
  ├── duration_coefficient (défaut 1.0, modifiable par l'admin — utilisé dès le MVP)
  ├── experience_level (Junior/Senior/Expert : grille de coefficients, V2)
  └── payment_records (1→N)

services
  └── media (1→N)                     ← images et vidéos

products
  ├── inventory (1→1)
  ├── media (1→N)                     ← images et vidéos
  └── order_items (1→N)

media                                 ← table polymorphe (service_id OU product_id)
  ├── type (image | video)
  ├── url, poster_url (vidéo), mime_type
  ├── size_bytes, duration_s (vidéo), width, height
  ├── status (processing | ready | failed)
  └── position (ordre d'affichage dans la galerie)

orders
  ├── order_items (1→N)
  ├── delivery_record (1→1)
  └── delivery_photos (1→N)          ← preuves de livraison R2

resources                             ← bacs de lavage, casques, etc.
  └── appointments (N→N via appointment_resources)

refresh_tokens                        ← gestion des sessions
  ├── user_id (→ clients ou hairdressers)
  ├── token_hash
  ├── expires_at
  └── revoked_at
```

### Droit à l'oubli — Anonymisation (pas suppression)

Le droit à l'oubli PIPEDA est implémenté par **anonymisation** afin de préserver l'intégrité des données financières et comptables.

```
Données anonymisées :
  ├── clients.nom          → "CLIENT_SUPPRIMÉ"
  ├── clients.email        → "anon_{id}@supprimé.kyja"
  ├── clients.telephone    → NULL
  └── clients.adresse      → NULL

Données conservées (intégrité légale / comptable) :
  ├── appointments         → conservés avec référence anonymisée
  ├── financial_records    → conservés et anonymisés (7 ans, fiscal)
  └── consent_records      → conservés intégralement (obligation légale)

Données supprimées :
  ├── Photos d'inspiration des demandes sur mesure (fichiers privés et lignes booking_photos)
  ├── Photos de profil
  ├── Préférences
  └── Adresses de livraison
```

> `ON DELETE CASCADE` uniquement sur les données sans valeur légale ou comptable (photos de profil, préférences, adresses de livraison).

### Règles d'intégrité importantes

- Transactions ACID pour toute création de rendez-vous (vérification atomique coiffeuse + équipement).
- `consent_records` : jamais supprimé ni anonymisé (preuve légale de consentement).
- `financial_records` : jamais supprimé, anonymisé lors du droit à l'oubli.

---

## Moteur de Réservation

### Deux modes de réservation

| Mode | Service concerné | Prix | Durée |
|---|---|---|---|
| `catalog` | Prestation avec `pricing_mode = estimate` et un `price` estimatif | Estimatif affiché, copié dans `estimated_price` | Calculée |
| `custom` | Prestation avec `pricing_mode = quote` (sans prix préétabli) | « À confirmer au salon », `estimated_price` NULL | Calculée |

Les deux modes partagent **le même moteur** : seul le point de départ du wizard et l'affichage du prix changent.

**Prestation intégrée « Demande sur mesure »** : `services.is_custom_request = true` (index unique partiel : une seule), créée par la migration à l'installation (120 min + 15 min de repos, `pricing_mode = quote`, sans prix), recréée à la volée si elle manque (`engine.custom_request_service`). Absente du catalogue public, de l'accueil et de la liste des coiffures ; modifiable dans l'admin (durée, repos). `GET /bookings/new?mode=custom` redirige directement vers son formulaire quand aucune autre prestation « sur devis » n'existe ; sinon la liste propose aussi « Autre demande ».
Une prestation « sur devis » a quand même une **durée de base** (nécessaire au calcul).

### Logique de calcul de durée (tous modes)

```
Durée calculée = arrondi_au_pas( Durée de base × Coefficient coiffeuse ) + Buffer Time
Pas du calendrier : 15 min (configurable)

Coefficient : champ hairdressers.duration_coefficient (défaut 1.0, modifiable par l'admin)
Grille indicative par niveau (V2) :
  - Junior  : 1.3  (30% de temps supplémentaire)
  - Senior  : 1.0  (durée standard)
  - Expert  : 0.85 (15% plus rapide)
```

* Fonction pure `compute_duration(base_min, coefficient, buffer_min, step=15)` dans `app/bookings/engine.py`, testée unitairement (cas limites : coefficient < 1, arrondi, buffer 0).
* Les valeurs utilisées sont **copiées dans le rendez-vous** (`base_duration_min`, `coefficient_used`, `buffer_min`, `computed_duration_min`) : modifier plus tard le coefficient d'une coiffeuse ne change pas les rendez-vous déjà pris.
* Mode « peu importe » : le moteur calcule la durée pour **chaque** coiffeuse candidate, retient les créneaux où elle tient entièrement, et assigne la première disponible.

### Photos d'inspiration (mode `custom`)

```
Sélection → aperçu local immédiat (URL blob:, aucun envoi nécessaire pour l'afficher)
         → POST /bookings/photos (fetch, en-tête X-CSRF-Token) : validation + ré-encodage WebP
         → brouillon booking_photos (appointment_id NULL), champ caché photo_ids dans le formulaire
Confirmation → les brouillons sont rattachés au rendez-vous DANS la transaction de réservation
Lecture → GET /bookings/photos/{id} : cliente propriétaire, admin, ou coiffeuse assignée (sinon 404)
```

* **Limites :** 10 photos par demande, 10 Mo par fichier envoyé (`image_max_mb`), 60 envois/heure/cliente, 1600 px de côté maximum après traitement.
* **Validation :** type détecté sur le contenu réel (`python-magic`) ; JPEG, PNG, WebP seulement ; image décodée et ré-encodée en WebP, ce qui **supprime tous les EXIF** (géolocalisation des photos de téléphone) ; protection contre les images piégées (limite de pixels).
* **Stockage privé :** dossier `private_files/` en développement, bucket R2 **distinct et sans accès public** (`r2_private_bucket`) en production. Jamais sous `/media-files`, jamais d'URL publique : tout passe par la route qui contrôle les droits.
* **Cycle de vie :** brouillons non utilisés purgés après 24 h (à chaque envoi, puis par une tâche Celery planifiée) ; suppression des fichiers lors du droit à l'oubli et de la suppression d'un rendez-vous.
* **CSP :** `img-src` autorise `blob:` pour les aperçus ; le script (`/static/js/booking-photos.js`) est servi depuis le site, sans JavaScript en ligne.

### Réservation sans connexion préalable

```
Parcours public : /bookings/new, /bookings/new/{id}, /bookings/calendar, /bookings/slots, POST /bookings/review, photos
Visiteuse → cookie `booking_draft` (HttpOnly, SameSite=Lax, Path=/bookings, 24 h) = jeton aléatoire ; seule son empreinte SHA-256 est en base
         → booking_drafts(token_hash, payload JSONB) garde le choix ; booking_photos.draft_token_hash garde les photos envoyées sans compte
Récapitulatif visiteuse → « Dernière étape » (Créer mon compte / J'ai déjà un compte) avec ?next=/bookings/resume
Connexion ou inscription → GET /bookings/resume : photos rattachées au compte (claim), choix revalidé (créneau encore libre ?), bouton Confirmer
Confirmation → brouillon et cookie supprimés ; session expirée au moment de confirmer → choix gardé, retour par /bookings/resume
```

* **Garde-fous des routes publiques :** consultation du calendrier/des heures plafonnée par IP (600/h) ; envois de photos des visiteuses plafonnés par jeton (60/h) **et** par IP (30/h) ; photos non rattachées purgées après 24 h ; CSRF exigé partout (jeton dans le formulaire ou en-tête).
* **Confidentialité :** une photo envoyée sans compte n'est lisible que par le porteur du cookie correspondant (et l'admin) ; un autre visiteur ou une autre cliente reçoit 404 ; `claim_photos` ne rattache que les photos portant le jeton de la requête.
* **Repli téléphone :** réglage `salon_phone` (admin), affiché si renseigné. L'admin crée un rendez-vous pour une cliente sans compte (`/admin/appointments/new`) : fiche cliente sans mot de passe utilisable (`tel-…@sans-courriel.kyja`), `create_booking(admin_override=True)` ignore seulement le délai minimal ; conflits, horaires et équipements restent contrôlés.
* **Évolution prévue :** réservation « invitée » (email vérifié + lien sécurisé) ou connexion par lien magique, dès que l'envoi d'emails (Celery + Resend) est installé. Le module `drafts.py` isole ce mécanisme pour faciliter ce changement.

### Calendrier mensuel (HTMX)

```
GET /bookings/calendar?service_id&hairdresser=any|id&month=YYYY-MM  → fragment HTML du mois
    jours cliquables = engine.available_days() : au moins un créneau libre (durée calculée complète, équipements, délai minimal, horizon)
    navigation bornée : mois courant → mois de l'horizon (60 j par défaut) ; mois invalide ou hors bornes → ramené dans les bornes
Clic sur un jour (radio `day`) → GET /bookings/slots → heures précises ; changer de coiffeuse → calendrier rechargé + heures remises à zéro (swap hors-bande)
```

Le calcul « y a-t-il au moins un créneau ce jour-là ? » s'arrête au premier créneau trouvé (`free_starts(..., limit=1)`). Si aucune coiffeuse n'a de disponibilités, le calendrier affiche un message explicite au lieu d'être vide.

### Prix estimatif et prix final

```
Réservation  : estimated_price = service.price (mode catalog) | NULL (mode custom)
Au salon     : final_price saisi par l'admin/coiffeuse à la fin de la prestation
Finance      : commissions et réconciliation calculées sur final_price uniquement
Écart        : final_price - estimated_price conservé pour analyse
```

### Détection de conflits (transaction atomique)

```sql
BEGIN;
  SET statement_timeout = '5s';       -- éviter les deadlocks prolongés

  -- 1. Vérifier disponibilité coiffeuse sur [début, début + durée calculée]
  SELECT ... FOR UPDATE;

  -- 2. Vérifier disponibilité équipements requis
  SELECT ... FOR UPDATE;

  -- 3. Si tout disponible : créer le rendez-vous
  INSERT INTO appointments ...;
  INSERT INTO appointment_resources ...;
COMMIT;
```

> Tous les calculs et validations sont effectués **avant** le `BEGIN` pour garder la transaction aussi courte que possible.

### Cache Redis des disponibilités

- TTL de 60 secondes pour les disponibilités affichées côté client (fragment HTMX).
- **Invalidation proactive** à chaque création ou annulation de rendez-vous (ne pas attendre l'expiration du TTL).
- La vérification finale reste toujours en base (source de vérité).

---

## Authentification & Autorisation

### Flux d'authentification (web — rendu serveur)

```
Login (formulaire HTML/HTMX) → FastAPI vérifie Argon2id
      → JWT Access Token (15 min) stocké en cookie HttpOnly + Secure + SameSite=Strict
      → Refresh Token (30 jours) stocké en cookie HttpOnly séparé, hashé en base (refresh_tokens)

Rotation des tokens :
  /auth/refresh → invalide l'ancien refresh token + génère un nouveau pair access + refresh

Révocation :
  Changement de mot de passe → tous les refresh_tokens de l'utilisateur révoqués
  Déconnexion globale        → idem
```

#### Renouvellement silencieux (`app/auth/session_refresh.py`)

Le cookie d'accès expire au bout de 15 minutes ; sans renouvellement, la cliente et l'admin étaient déconnectés. Un middleware (le plus interne de la pile) renouvelle la session **avant** que la requête n'atteigne la route :

```
Requête avec cookie d'accès absent/expiré + cookie refresh présent
  → silent_refresh() : le refresh est valide ?  oui → rotation (ancien révoqué, successeur créé, 30 j repartent à zéro)
  → nouveau cookie d'accès injecté dans la requête (la route voit la session) ET posé sur la réponse
Requête simultanée portant le même ancien refresh (images, HTMX) → tolérée 15 s SI l'ancien jeton a été remplacé par
  une rotation dont le successeur est valide : elle reçoit seulement un cookie d'accès (pas de fausse alerte de vol)
Jeton rejoué hors tolérance, ou révoqué par déconnexion / « déconnexion partout » / alerte de vol → toutes les sessions coupées
```

* Le cookie refresh est envoyé à **toutes** les routes (`Path=/`, HttpOnly, SameSite=Strict, Secure hors dev), condition du renouvellement.
* **Jamais de renouvellement** pour `/static`, `/media-files`, `/health`, ni pour `/auth/logout`, `/auth/logout-all`, `/auth/refresh` : une déconnexion ne doit pas être défaite par le renouvellement ; `/auth/refresh` reste strict (clients mobiles).
* `refresh_tokens.replaced_by_id` relie un jeton à son successeur : c'est ce qui distingue une rotation (tolérée un court instant) d'une révocation volontaire (jamais tolérée).
* Aucun surcoût pour les visiteuses sans cookie ; une requête de renouvellement par session et par tranche de 15 minutes d'activité.

> **Pourquoi cookie HttpOnly plutôt que localStorage/Authorization header côté web :** élimine le risque de vol de token par XSS, cohérent avec un rendu serveur où le navigateur envoie les cookies automatiquement à chaque requête HTMX.

### Flux d'authentification (future app mobile — React Native/Expo, Phase 3+)

```
Login → FastAPI vérifie Argon2id → JWT Access Token + Refresh Token renvoyés en JSON
      → stockés en stockage sécurisé natif (Keychain/Keystore via Expo SecureStore)
      → Access Token envoyé en header Authorization: Bearer <token>
```

Même backend, même table `refresh_tokens`, même logique de rotation/révocation — seul le mode de transport du token change (cookie vs header JSON) selon le client.

### CSRF — spécifique au rendu serveur avec cookies

Puisque les requêtes HTMX/formulaires envoient automatiquement les cookies de session, **toutes les routes HTML qui modifient un état** (POST/PUT/PATCH/DELETE) sont protégées par double-submit cookie :
- Un token CSRF est injecté dans chaque page via une balise `<meta>` et lu par Alpine/HTMX (`hx-headers`).
- Comparé au cookie CSRF à chaque requête mutante.
- Les routes JSON pures (API mobile, Bearer token) n'ont pas besoin de cette protection — le Bearer token suffit.

### Rôles

| Rôle | Accès |
|---|---|
| `client` | Profil, réservations, commandes, fidélité |
| `hairdresser` | Propre agenda, propre finance, propre portfolio |
| `admin` | Tout + validation portfolios, réconciliation, POS, inventaire |

### Sécurité

- Mots de passe : hachage **Argon2id** (`argon2-cffi`), paramètres minimaux OWASP m=19 Mio, t=2, p=1, à ajuster sur le VPS (cible ~100–250 ms par vérification). Les hachages bcrypt éventuels sont ré-hachés en Argon2id à la connexion.
- Rate limiting : `/auth/login` 5 tentatives / 15 min par IP **et** par email (via Redis) ; limites plus strictes sur inscription, reset et upload. Retour d'erreur identique que le compte existe ou non.
- HTTPS obligatoire (Certbot + Let's Encrypt sur Nginx)
- Headers de sécurité posés **aussi dans l'application** (middleware), pas seulement dans Nginx : `CSP` stricte avec nonce par requête, `HSTS`, `X-Content-Type-Options: nosniff`, `Referrer-Policy`, `Permissions-Policy`, `X-Frame-Options`/`frame-ancestors`
- `TrustedHostMiddleware` (hôtes autorisés) et CORS à origines explicites, jamais `*`
- Protection CSRF sur toutes les routes HTML mutantes (double-submit signé, voir ci-dessus, aucune dépendance à une classe Starlette supposée) + cookies `SameSite` + sur `/auth/refresh`
- Contrôle d'accès (OWASP A01) : vérification du rôle ET de la propriété de la ressource sur chaque route (une cliente ne lit que ses rendez-vous), tests dédiés
- Gestion des erreurs (OWASP A10) : handler global, aucune stacktrace côté client, échec « fermé » en cas d'erreur d'autorisation
- Chaîne d'approvisionnement (OWASP A03) : dépendances épinglées, audit `pip-audit` en CI, analyse statique `bandit`, mises à jour planifiées
- Uploads : liste blanche de types vérifiés sur le **contenu réel** (`python-magic`), jamais sur l'extension ou le Content-Type ; noms de fichiers régénérés (UUID) ; stockage hors dossier web ; `ffprobe`/`ffmpeg` lancés dans un processus à ressources limitées (timeout, mémoire) car l'analyse de médias non fiables est elle-même un risque
- Secrets uniquement via variables d'environnement (jamais dans le dépôt) ; journaux sans données personnelles
- Données sensibles chiffrées au repos : numéros de téléphone, adresses (PostgreSQL `pgcrypto`)

---

## Notifications

Tous les envois sont traités de manière **asynchrone via Celery**. En cas d'échec, retry automatique avec backoff exponentiel (3 tentatives maximum).

### Email — Resend

- Confirmation de réservation
- Rappel de rendez-vous (J-1 et H-2) — planifié par Celery Beat
- Statuts de commande et livraison
- Bienvenue à l'inscription

### Push — Web Push API (PWA)

- Service worker écrit manuellement (pas de plugin `next-pwa` — enregistrement et gestion du cache à la main dans `static/sw.js`)
- Statuts de livraison en temps-réel ("En route", "Livré")
- Rappels de rendez-vous
- Offres promotionnelles (opt-in explicite requis — PIPEDA)

> Lors du passage à React Native (Phase 3+), les notifications push migrent vers **Expo Push Notifications** sans changement côté backend.

---

## Stratégie Mobile

### Phase 1-2 : PWA (rendu serveur + service worker manuel)

- `manifest.json` + service worker écrits à la main (pas de dépendance à un plugin Next.js)
- Installable sur iOS et Android depuis le navigateur
- Cache des pages de base pour fonctionnement hors-ligne partiel
- Notifications push via **Web Push API**
- Zéro coût de développement supplémentaire, zéro dépendance Node

### Phase 3+ : Application Native (React Native + Expo)

- Consomme les **routes JSON** déjà existantes du backend FastAPI (déjà construites pour l'admin et prêtes à être étendues)
- Même backend, même modèle d'auth JWT (transport différent : header au lieu de cookie)
- Publication sur App Store et Google Play
- Notifications push via Expo Push Notifications

---

## Conformité PIPEDA (Canada)

| Exigence | Implémentation |
|---|---|
| Consentement marketing explicite | Opt-in checkbox à l'inscription, loggué dans `consent_records` |
| Droit à l'oubli | Anonymisation des données personnelles (voir schéma BDD) |
| Accès aux données personnelles | Endpoint `GET /clients/me/export` → JSON de toutes les données |
| Chiffrement en transit | HTTPS obligatoire (Let's Encrypt) |
| Chiffrement au repos | `pgcrypto` pour données PII sensibles (téléphone, adresse) |
| Audit trail financier | `financial_records` anonymisés, jamais supprimés |
| Plan de réponse aux brèches | Procédure documentée : détection → notification CPVP sous 72h → notification clientes affectées |
| Localisation des données | VPS avec datacenter au Canada (OVH Beauharnois QC recommandé) — documenté dans politique de confidentialité |

### Politique de rétention des données

| Type de donnée | Durée de rétention |
|---|---|
| Données clients actifs | Durée de la relation + 2 ans |
| `financial_records` | 7 ans (obligations fiscales canadiennes) |
| Preuves de livraison (photos) | 7 ans |
| Logs d'audit | 7 ans |
| Données clients inactifs anonymisées | Purge automatique via tâche Celery mensuelle |

---

## Observabilité

### Logging

- `structlog` (Python) : logs JSON structurés sur tous les endpoints critiques
- Niveaux : `INFO` pour les opérations normales, `ERROR` pour les exceptions, `WARNING` pour les tentatives suspectes (auth, rate limit)

### Monitoring

- Health check : `GET /health` → vérifie PostgreSQL + Redis
- Uptime monitoring : UptimeRobot (gratuit) → alerte email/SMS si indisponible

### Alertes critiques

- Taux d'erreur 5xx > 1% → alerte immédiate
- Espace disque VPS < 20% → alerte
- Backup échoué → alerte

---

## Stratégie de Tests

| Type | Outil | Cible |
|---|---|---|
| Unitaires | pytest | Calcul durées, commissions, coefficients, conflits |
| Intégration | pytest + PostgreSQL test | Transactions atomiques du moteur de réservation |
| End-to-end | Playwright | Réservation, droit à l'oubli, POS — fonctionne nativement sur du HTML serveur, pas besoin d'attendre l'hydration JS |
| Contrat API | schemathesis | Validation spec OpenAPI générée par FastAPI (routes JSON) |

---

## Feuille de Route Technique

### MVP (Phase 1)
- [ ] Projet FastAPI initialisé avec Jinja2 + HTMX + Alpine.js + Tailwind (CLI standalone)
- [ ] Schéma PostgreSQL complet + migrations Alembic
- [ ] Auth (inscription / login / cookie session JWT / refresh tokens / révocation / CSRF)
- [ ] Catalogue services et produits (pages Jinja2)
- [ ] Moteur de réservation : 2 modes (catalogue à prix estimatif / sur mesure), durée calculée par coiffeuse, conflits coiffeuse + équipement, wizard HTMX
- [ ] Prix estimatif → prix final saisi au salon
- [ ] Profil client + historique
- [ ] Interface admin de base (HTMX pour validations en direct)
- [ ] Celery + Celery Beat configurés
- [ ] Notifications email (Resend) via Celery
- [ ] Backups automatisés PostgreSQL → R2
- [ ] Health check + UptimeRobot
- [ ] Déploiement VPS unique + Nginx + SSL
- [ ] PWA activée (manifest + service worker manuel)

### V1 (Phase 2)
- [ ] Module RH coiffeuses (portail + portfolio privé + validation admin)
- [ ] Module finance (commissions + loyers + réconciliation)
- [ ] Livraison maison (zonage + preuves photo async)
- [ ] Inventaire hybride (online + POS sync)
- [ ] Notifications push Web Push API

### V2 (Phase 3)
- [ ] Grille de coefficients par niveau d'expérience (Junior/Senior/Expert) — le coefficient par coiffeuse existe dès le MVP
- [ ] Programme de fidélité (points automatiques)
- [ ] POS rapide ("Vente Comptoir" + popup inventaire 10s via Alpine.js)
- [ ] Routes JSON étendues pour React Native (Expo) + Expo Push Notifications

---

## Impact Budgétaire du Changement d'Architecture

| Poste | Avant (Next.js + Vercel) | Après (FastAPI + Jinja2/HTMX) |
|---|---|---|
| Hébergement frontend | Vercel Pro (~27 $CAD/mois pour usage commercial) | Inclus dans le VPS déjà payé — **0 $ supplémentaire** |
| Dépendances Node en prod | Build Next.js à chaque déploiement | Aucune — binaire Tailwind standalone uniquement |
| Pipeline de déploiement | 2 services séparés (Vercel + VPS) | 1 seul service (VPS) |

---

## Points à Définir

| Point | Statut |
|---|---|
| Nom de domaine (`kyjabeaute.ca` ?) | À confirmer |
| Région exacte du VPS (pour documentation PIPEDA) | À confirmer lors de l'achat |
| Intégration paiement en ligne (Phase 2+) | Non décidé — Stripe ou PayPal Canada |
| Langue(s) de l'interface (FR seulement, ou FR + EN ?) | À confirmer |
| Bibliothèque de composants Tailwind (recettes HTML réutilisables pour remplacer shadcn/ui) | À construire au fil du développement |
