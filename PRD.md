# Cahier des Charges : Application Digitale "KYJA BEAUTÉ"

## 1. Vision et Objectifs
Concevoir une plateforme numérique robuste et élégante pour le salon **KYJA BEAUTÉ**, spécialisé dans la coiffure et la vente de produits de beauté[cite: 1]. L'application doit offrir une expérience utilisateur haut de gamme, gérer intelligemment les ressources physiques et humaines, et assurer une logistique de vente fluide[cite: 1].

---

## 2. Exigences Non-Fonctionnelles (Qualité et Sécurité)

### **A. Performance et Scalabilité**
* **Concurrence élevée :** L'architecture doit supporter de nombreuses connexions simultanées de clientes sans ralentissement (utilisation de cache, optimisation des requêtes SQL).
* **Rapidité :** Temps de chargement minimal pour les galeries d'images haute résolution et les vidéos des coiffures et produits de **KYJA BEAUTÉ**.
* **Vidéos :** Lecture fluide sur mobile (streaming progressif, chargement différé, miniature/poster affichée avant lecture, formats compressés type MP4/WebM, durée maximale de **3 minutes** par vidéo, taille maximale à l'upload de **100 Mo** par défaut, modifiable au besoin par l'admin).

### **B. Sécurité Applicative**
* **Protection des données :** Chiffrement des données au repos et en transit (SSL/TLS).
* **Authentification :** Gestion sécurisée des mots de passe (hachage fort) et protection contre les attaques par force brute.
* **Bonnes pratiques :** Respect rigoureux des standards de sécurité web pour prévenir les failles (Injection SQL, XSS, CSRF).

### **C. Design et Expérience Utilisateur (UI/UX)**
* **Esthétique attractive :** Design moderne, épuré et visuellement percutant, reflétant l'image de marque "Luxe & Beauté" de **KYJA BEAUTÉ**.
* **Responsive :** Expérience fluide et parfaite sur mobile, tablette et ordinateur.

---

## 3. Planification des Versions (Feuille de Route)

### **Phase 1 : MVP (Produit Minimum Viable)**
* **Catalogue :** Présentation par tuiles des prestations de **KYJA BEAUTÉ**, des articles et des coiffures avec galeries multimédias (**images et vidéos**) et prix dynamiques[cite: 1]. Chaque prestation, produit ou coiffure peut associer une ou plusieurs photos et/ou vidéos (ex: démonstration d'une coiffure, utilisation d'un produit).
* **Réservation :** Calendrier lié aux disponibilités des coiffeuses du salon[cite: 1], avec **deux modes de réservation** (voir 4.A) : (1) choix d'une coiffure du catalogue avec **prix estimatif** ; (2) réservation **sur mesure**, sans prix préétabli. Dans les deux cas, la **durée est calculée** (durée de base × coefficient de la coiffeuse + temps de repos) et sert à proposer les créneaux.
* **Profil Client :** Inscription conforme aux lois canadiennes (LPRPDE), historique des prestations et droit à l'oubli[cite: 1].
* **Paiement :** Indication "Paiement au salon (Cash/Interac)"[cite: 1]. Le prix affiché à la réservation est **estimatif** : le montant final est saisi au salon à la fin de la prestation.

### **Phase 2 : V1 (Intelligence & Logistique)**
* **Le Cerveau :** Matrice de ressources pour **KYJA BEAUTÉ** (bacs de lavage/casques) et "Buffer Time" (temps de repos)[cite: 1].
* **Module RH :** Portail d'inscription pour les coiffeuses indépendantes souhaitant travailler chez **KYJA BEAUTÉ**, avec dépôt de portfolio privé[cite: 1].
* **Finance :** Module de calcul des gains et de réconciliation des paiements manuels (Validation Admin)[cite: 1].
* **Livraison Maison :** Zonage par rayon et preuve de dépôt par photo pour les produits vendus par le salon[cite: 1].

### **Phase 3 : V2 (Optimisation)**
* **Performance RH :** Grille de coefficients de temps par niveau d'expérience (Junior / Senior / Expert)[cite: 1]. Le coefficient propre à chaque coiffeuse est déjà utilisé dès le MVP pour calculer les durées (valeur par défaut 1,0, modifiable par l'admin) ; la V2 ajoute la grille par niveau.
* **Fidélité :** Programme de points automatique activable sur le profil client[cite: 1].
* **Vente Rapide (POS) :** Bouton "Vente Comptoir" en admin avec popup de rappel d'inventaire de 10s pour les ventes physiques au salon[cite: 1].

---

## 4. Spécifications Fonctionnelles Détaillées

### **A. Moteur de Réservation de KYJA BEAUTÉ**

#### Deux modes de réservation
| | **Mode 1 — Par coiffure (catalogue)** | **Mode 2 — Sur mesure** |
|---|---|---|
| Point de départ | La cliente choisit une coiffure du catalogue qui a **déjà un prix estimatif** | Aucune coiffure à choisir : la cliente arrive **directement sur le formulaire** de la prestation intégrée « Demande sur mesure » (le salon peut aussi proposer des prestations « sur devis » en plus) |
| Prix affiché | Prix estimatif du catalogue, libellé « **estimé** » | « **Prix à confirmer au salon** » (aucun montant promis) |
| Informations demandées | Coiffure, coiffeuse (ou « peu importe »), date, créneau | Idem, plus une **zone de texte pour décrire la demande** (obligatoire) et **jusqu'à 10 photos d'inspiration** (facultatives) |
| Durée | Calculée (voir ci-dessous) | Calculée (voir ci-dessous), à partir de la durée de base de la prestation sur devis |

#### Parcours de réservation (identique pour les deux modes après l'étape 1)
1. **Choix de départ** : coiffure du catalogue (mode 1) ou demande sur mesure (mode 2).
2. **Choix de la coiffeuse** : une coiffeuse précise ou « peu importe » (le système assigne la première disponible).
3. **Durée calculée** : affichée à la cliente **par coiffeuse** (ex: « 2 h 15 avec Marie, 1 h 50 avec Sonia »), car le coefficient diffère.
4. **Choix du jour puis de l'heure** : un **calendrier mensuel** met en évidence les jours où au moins un créneau est libre (les autres sont grisés, navigation de mois en mois dans la limite de l'horizon de réservation) ; en cliquant un jour, la cliente voit les **heures précises** où la durée **complète** tient. Changer de coiffeuse met à jour les jours disponibles.
5. **Récapitulatif** : coiffure, coiffeuse, date, heure de début et de fin, durée, prix **estimatif** (ou « à confirmer »), mention « Paiement au salon (Cash/Interac) ».
6. **Confirmation** : transaction atomique (voir Gestion des Conflits) puis email de confirmation.

#### Règles
* **Calcul du temps :** $$\text{Durée} = (\text{Temps Prestation} \times \text{Coeff. Coiffeuse}) + \text{Buffer Time}$$[cite: 1]. Durée arrondie au pas du calendrier (ex: 15 min). Le calcul s'applique **à tous les modes**.
* **Demande sur mesure intégrée :** une prestation « Demande sur mesure » existe dès l'installation (durée de base 2 h et repos 15 min par défaut, modifiables par l'admin dans Prestations). Elle n'apparaît pas dans le catalogue public. Sa durée de base alimente le calcul comme pour toute prestation.
* **Mémorisation du calcul :** chaque rendez-vous conserve les valeurs utilisées (durée de base, coefficient, temps de repos, durée calculée) pour rester cohérent si le coefficient d'une coiffeuse change ensuite.
* **Prix estimatif :** le prix du catalogue est une estimation, jamais un engagement. Le **prix final** est saisi au salon (admin ou coiffeuse) à la fin de la prestation ; c'est lui qui alimente la finance (commissions, réconciliation). L'écart estimé/final est conservé.
* **Gestion des Conflits :** Vérification simultanée de la disponibilité de la coiffeuse ET de l'équipement nécessaire (ex: bac de lavage) avant confirmation[cite: 1], sur toute la durée calculée (temps de repos inclus).
* **Mode « peu importe » :** si plusieurs coiffeuses conviennent, assignation à la première disponible ; la cliente voit le nom de la coiffeuse dans la confirmation.
* **Photos d'inspiration (mode 2) :** jusqu'à **10 photos** par demande (JPEG, PNG ou WebP, 10 Mo maximum chacune). Un **aperçu s'affiche immédiatement** dès la sélection, avant même la fin de l'envoi ; chaque photo peut être retirée avant la confirmation. Les photos sont **privées** : visibles uniquement de la cliente, de l'admin et de la coiffeuse assignée ; les métadonnées (dont la géolocalisation) sont supprimées. Les photos jamais rattachées à un rendez-vous sont effacées après 24 h ; elles sont supprimées avec le droit à l'oubli.
* **Demandes sur mesure :** la description et les photos sont visibles de la coiffeuse et de l'admin ; l'admin peut ajuster la durée avant la prestation (la cliente est notifiée).
* **Annulation / modification :** la cliente peut annuler depuis son profil ; le créneau est libéré immédiatement (cache de disponibilités invalidé).

### **B. Gestion des Coiffeuses Indépendantes**
* **Modèle financier :** Calcul en temps réel des commissions ou des loyers de chaise selon la grille tarifaire de **KYJA BEAUTÉ**[cite: 1], à partir du **prix final** saisi au salon (et non du prix estimatif).
* **Confidentialité :** Les preuves de compétences (diplômes/expérience) sont réservées exclusivement à la validation administrative de **KYJA BEAUTÉ**[cite: 1].

### **C. Logistique et Inventaire**
* **Livraison :** Notifications d'étapes ("En route", "Arrivé") avec preuve photographique obligatoire pour garantir la réception des produits[cite: 1].
* **Inventaire Hybride :** Rappel automatique de mise à jour après chaque vente au comptoir pour éviter les ruptures de stock en ligne[cite: 1].

---

## 5. Conformité Légale (Canada)
* **Consentement :** Opt-in explicite requis pour les communications marketing de **KYJA BEAUTÉ**[cite: 1].
* **Protection :** Accès direct pour la cliente à la suppression totale de ses données personnelles[cite: 1].
