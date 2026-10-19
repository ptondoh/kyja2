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
Réservation (wizard en étapes) :
  1. GET /bookings/new                     → page complète (étape "choisir un service")
  2. Clic service → HTMX GET /bookings/availability?service_id=X&date=Y
                                            → fragment HTML (grille de créneaux dispo)
  3. Clic créneau → Alpine stocke la sélection en état local (pas de requête serveur)
  4. Soumission finale → HTMX POST /bookings/
                                            → transaction atomique + fragment de confirmation

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
  ├── resources (N→N via appointment_resources)   ← détection conflits
  └── financial_record (1→1)

hairdressers
  ├── availability_slots (1→N)
  ├── portfolio_files (1→N)           ← pending ou approved
  ├── experience_level (coefficient)
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

### Logique de calcul de durée

```
Durée totale = (Durée de base du service × Coefficient coiffeuse) + Buffer Time

Coefficients :
  - Junior  : 1.3  (30% de temps supplémentaire)
  - Senior  : 1.0  (durée standard)
  - Expert  : 0.85 (15% plus rapide)
```

### Détection de conflits (transaction atomique)

```sql
BEGIN;
  SET statement_timeout = '5s';       -- éviter les deadlocks prolongés

  -- 1. Vérifier disponibilité coiffeuse
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
- [ ] Moteur de réservation (conflits coiffeuse + équipement, wizard HTMX)
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
- [ ] Coefficients d'expérience coiffeuses (Junior/Senior/Expert)
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
