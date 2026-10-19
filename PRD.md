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
* **Réservation :** Calendrier de base lié aux disponibilités des coiffeuses du salon[cite: 1].
* **Profil Client :** Inscription conforme aux lois canadiennes (LPRPDE), historique des prestations et droit à l'oubli[cite: 1].
* **Paiement :** Indication "Paiement au salon (Cash/Interac)"[cite: 1].

### **Phase 2 : V1 (Intelligence & Logistique)**
* **Le Cerveau :** Matrice de ressources pour **KYJA BEAUTÉ** (bacs de lavage/casques) et "Buffer Time" (temps de repos)[cite: 1].
* **Module RH :** Portail d'inscription pour les coiffeuses indépendantes souhaitant travailler chez **KYJA BEAUTÉ**, avec dépôt de portfolio privé[cite: 1].
* **Finance :** Module de calcul des gains et de réconciliation des paiements manuels (Validation Admin)[cite: 1].
* **Livraison Maison :** Zonage par rayon et preuve de dépôt par photo pour les produits vendus par le salon[cite: 1].

### **Phase 3 : V2 (Optimisation)**
* **Performance RH :** Coefficient de temps variable selon l'expérience de la coiffeuse (Junior vs Senior)[cite: 1].
* **Fidélité :** Programme de points automatique activable sur le profil client[cite: 1].
* **Vente Rapide (POS) :** Bouton "Vente Comptoir" en admin avec popup de rappel d'inventaire de 10s pour les ventes physiques au salon[cite: 1].

---

## 4. Spécifications Fonctionnelles Détaillées

### **A. Moteur de Réservation de KYJA BEAUTÉ**
* **Gestion des Conflits :** Vérification simultanée de la disponibilité de la coiffeuse ET de l'équipement nécessaire (ex: bac de lavage) avant confirmation[cite: 1].
* **Calcul du temps :** $$\text{Durée} = (\text{Temps Prestation} \times \text{Coeff. Coiffeuse}) + \text{Buffer Time}$$[cite: 1].

### **B. Gestion des Coiffeuses Indépendantes**
* **Modèle financier :** Calcul en temps réel des commissions ou des loyers de chaise selon la grille tarifaire de **KYJA BEAUTÉ**[cite: 1].
* **Confidentialité :** Les preuves de compétences (diplômes/expérience) sont réservées exclusivement à la validation administrative de **KYJA BEAUTÉ**[cite: 1].

### **C. Logistique et Inventaire**
* **Livraison :** Notifications d'étapes ("En route", "Arrivé") avec preuve photographique obligatoire pour garantir la réception des produits[cite: 1].
* **Inventaire Hybride :** Rappel automatique de mise à jour après chaque vente au comptoir pour éviter les ruptures de stock en ligne[cite: 1].

---

## 5. Conformité Légale (Canada)
* **Consentement :** Opt-in explicite requis pour les communications marketing de **KYJA BEAUTÉ**[cite: 1].
* **Protection :** Accès direct pour la cliente à la suppression totale de ses données personnelles[cite: 1].
