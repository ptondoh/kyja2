# À faire — KYJA BEAUTÉ

Liste vivante des choses qui attendent un accès, une décision ou du temps. Les cases cochées sont faites.

## En attente d'accès externes (rien n'est envoyé pour de vrai tant qu'ils manquent)

- [ ] **Clé Resend (courriel)** : créer un compte Resend, vérifier le domaine du salon (enregistrements SPF/DKIM),
      puis renseigner `RESEND_API_KEY` et `EMAIL_FROM` dans `.env`. D'ici là, les courriels sont enregistrés
      en « aperçu » et visibles dans **Admin → Messages envoyés**. Une fois la clé en place :
  - [ ] envoyer un vrai courriel de test et vérifier l'arrivée (hors courriers indésirables) ;
  - [ ] ajouter la page de confirmation de désabonnement pour les messages marketing (LCAP/CASL).
- [ ] **Compte Twilio (SMS)** : créer un compte, acheter un numéro canadien, puis renseigner `TWILIO_ACCOUNT_SID`,
      `TWILIO_AUTH_TOKEN`, `TWILIO_FROM` dans `.env`. D'ici là, les SMS sont en « aperçu » (Admin → Messages envoyés).
  - [ ] envoyer un vrai SMS de test ;
  - [ ] vérifier le texte de consentement SMS avec les règles canadiennes (LCAP/CASL) ;
  - [ ] gérer la réponse « STOP » des clientes.
- [ ] **`PUBLIC_BASE_URL`** : adresse publique du site (ex. `https://www.kyjabeaute.ca`) pour que les liens des
      courriels et SMS soient valides. En développement : `http://localhost:8000`.
- [ ] **Redis + Celery : installer Redis et tester pour de vrai.** Le code est prêt et testé sans Redis
      (envoi des messages en file avec reprises, rappels la veille et 2 h avant, annulation des prix sans réponse,
      nettoyage des brouillons). Reste : installer Redis (Memurai ou WSL sous Windows), mettre `USE_CELERY=true`,
      lancer `celery -A app.tasks.celery_app worker -B -P solo -l info` (voir README) et vérifier un rappel réel.
- [ ] **Cloudflare R2** : créer les deux buckets (`kyja-beaute` public, `kyja-private` privé) et tester avec de vrais fichiers.
- [ ] **Alertes du navigateur quand le site est fermé** (Web Push : service worker + clés VAPID). Aujourd'hui l'alerte
      fonctionne seulement quand le site est ouvert dans le navigateur.

## Mise en production
- [ ] Serveur (VPS au Canada), Nginx, HTTPS, `SECRET_KEY` solide, sauvegardes de la base testées, journaux.
- [ ] Limitation des tentatives de connexion partagée entre processus (Redis), aujourd'hui en mémoire.
- [ ] Chiffrement du téléphone et de l'adresse dans la base (demande des droits super-utilisateur PostgreSQL ou un chiffrement applicatif).

## Fonctions prévues (cahier des charges, phases 2 et 3)
- [ ] Restaurer une catégorie depuis sa sauvegarde ZIP (aujourd'hui : copie lisible, sans import).
- [ ] Portail des coiffeuses indépendantes (inscription, portfolio privé), commissions et finance.
- [ ] Livraison à domicile, fidélité, vente au comptoir, application mobile.
- [ ] Produits : archivage au lieu de la suppression dès qu'il y aura des commandes.

## Fait
- [x] Rappels de rendez-vous (la veille et 2 h avant), envoi des messages et nettoyages par Celery (repli intégré sans Redis).
- [x] Prix final d'une demande sur mesure : proposition, lien de confirmation, alertes (site, navigateur, courriel/SMS en aperçu).
- [x] Agenda de la coiffeuse (« Mon agenda »).
- [x] Carrousel des photos et vidéos.
- [x] Changer la date ou l'heure d'un rendez-vous confirmé (cliente), avec alerte à la coiffeuse et à l'admin.
- [x] Pagination et filtres des listes (rendez-vous, prestations, produits, coiffeuses, messages, alertes).
- [x] Menu actif, cloche rouge, carrousel avec flèches, fenêtre de confirmation centrée, format des vidéos respecté.
- [x] Réservation en ligne (2 modes), calendrier, durée calculée, photos d'inspiration.
- [x] Profil client, export des données, droit à l'oubli.
- [x] Catégories, suppression/archivage, sauvegarde de catégorie, stock faible, avis de prix estimatif.
