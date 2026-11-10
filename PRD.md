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

#### Compte et connexion : au dernier moment
Tout le parcours (choix, calendrier, heures, description, photos) est **public** : la cliente n'a pas besoin de compte pour explorer ni préparer sa réservation. Le **compte est demandé uniquement à la confirmation** (« Dernière étape : votre compte ») : il sert à retrouver, modifier ou annuler le rendez-vous et à protéger les photos. Le choix de la cliente (prestation, coiffeuse, créneau, description, photos) est **conservé** pendant qu'elle se connecte ou crée son compte, puis elle revient directement au récapitulatif. Si la cliente ne veut pas de compte, elle peut **réserver par téléphone** : le numéro du salon (réglable par l'admin) est affiché sur la page de connexion, le récapitulatif et le pied de page, et l'admin peut **saisir le rendez-vous à la main** dans le même calendrier. *(Décision provisoire : une réservation « invitée » sans mot de passe est envisagée une fois l'envoi d'emails en place.)*

#### Parcours de réservation (identique pour les deux modes après l'étape 1)
1. **Choix de départ** : coiffure du catalogue (mode 1) ou demande sur mesure (mode 2).
2. **Choix de la coiffeuse** : une coiffeuse précise ou « peu importe » (le système assigne la première disponible).
3. **Durée calculée** : affichée à la cliente **par coiffeuse** (ex: « 2 h 15 avec Marie, 1 h 50 avec Sonia »), car le coefficient diffère.
4. **Choix du jour puis de l'heure** : un **calendrier mensuel** met en évidence les jours où au moins un créneau est libre (les autres sont grisés, navigation de mois en mois dans la limite de l'horizon de réservation) ; en cliquant un jour, la cliente voit les **heures précises** où la durée **complète** tient. Changer de coiffeuse met à jour les jours disponibles.
5. **Récapitulatif** : coiffure, coiffeuse, date, heure de début et de fin, durée, prix **estimatif** (ou « à confirmer »), mention « Paiement au salon (Cash/Interac) ».
6. **Confirmation** : connexion ou création de compte si ce n'est pas déjà fait, puis transaction atomique (voir Gestion des Conflits) et email de confirmation.

#### Règles
* **Prix estimatif — avis à la cliente :** pour les coiffures du catalogue, le récapitulatif affiche un avis permanent, et une **fenêtre de confirmation** s'ouvre au moment de confirmer : « le prix est estimatif et peut varier après analyse des photos et des vidéos ; le montant final est confirmé au salon ». L'avis est répété sur la page du rendez-vous tant que le prix final n'est pas saisi.
* **Calcul du temps :** $$\text{Durée} = (\text{Temps Prestation} \times \text{Coeff. Coiffeuse}) + \text{Buffer Time}$$[cite: 1]. Durée arrondie au pas du calendrier (ex: 15 min). Le calcul s'applique **à tous les modes**.
* **Demande sur mesure intégrée :** une prestation « Demande sur mesure » existe dès l'installation (durée de base 2 h et repos 15 min par défaut, modifiables par l'admin dans Prestations). Elle n'apparaît pas dans le catalogue public. Sa durée de base alimente le calcul comme pour toute prestation.
* **Mémorisation du calcul :** chaque rendez-vous conserve les valeurs utilisées (durée de base, coefficient, temps de repos, durée calculée) pour rester cohérent si le coefficient d'une coiffeuse change ensuite.
* **Prix estimatif :** le prix du catalogue est une estimation, jamais un engagement. Le **prix final** est saisi au salon (admin ou coiffeuse) à la fin de la prestation ; c'est lui qui alimente la finance (commissions, réconciliation). L'écart estimé/final est conservé.
* **Gestion des Conflits :** Vérification simultanée de la disponibilité de la coiffeuse ET de l'équipement nécessaire (ex: bac de lavage) avant confirmation[cite: 1], sur toute la durée calculée (temps de repos inclus).
* **Mode « peu importe » :** si plusieurs coiffeuses conviennent, assignation à la première disponible ; la cliente voit le nom de la coiffeuse dans la confirmation.
* **Photos d'inspiration (mode 2) :** jusqu'à **10 photos** par demande (JPEG, PNG ou WebP, 10 Mo maximum chacune). Un **aperçu s'affiche immédiatement** dès la sélection, avant même la fin de l'envoi ; chaque photo peut être retirée avant la confirmation. Les photos sont **privées** : visibles uniquement de la cliente, de l'admin et de la coiffeuse assignée ; les métadonnées (dont la géolocalisation) sont supprimées. Les photos jamais rattachées à un rendez-vous sont effacées après 24 h ; elles sont supprimées avec le droit à l'oubli.
* **Demandes sur mesure :** la description et les photos sont visibles de la coiffeuse et de l'admin ; l'admin peut ajuster la durée avant la prestation (la cliente est notifiée).
* **Annulation / modification :** la cliente peut annuler depuis son profil ; le créneau est libéré immédiatement (cache de disponibilités invalidé).
* **Prix final d'une demande sur mesure (accord de la cliente) :** à la réservation, le créneau est **réservé** et le rendez-vous est « **Prix à proposer** ». L'**admin ou la coiffeuse assignée** étudie la description et les photos, puis **propose le prix final**. La cliente est alors avertie et reçoit un **lien** pour **accepter** (le rendez-vous devient « Confirmé ») ou **refuser** (le rendez-vous est annulé et le créneau libéré). Elle peut aussi répondre depuis la page de son rendez-vous. Le lien : valable **7 jours** (réglable par l'admin) sans dépasser la date du rendez-vous, **à usage unique**, remplacé si un nouveau prix est proposé ; sans réponse, le rendez-vous est annulé automatiquement et l'équipe est prévenue. L'accord ne vaut pas paiement : le paiement reste au salon.
* **Alertes — la cliente choisit :** dans son profil, la cliente choisit comment être avertie : **dans le site** (cloche « Alertes »), **alerte du navigateur** quand le site est ouvert, **courriel**, **SMS** (numéro valide requis). Le personnel reçoit toujours les alertes du site (nouvelle demande, prix accepté ou refusé, annulation). *Tant que les comptes Resend (courriel) et Twilio (SMS) ne sont pas configurés, ces deux canaux fonctionnent en « aperçu » : le message est écrit dans Admin → Messages envoyés, sans être expédié.*
* **Agenda de la coiffeuse :** la coiffeuse a sa propre page « **Mon agenda** » avec **tous les rendez-vous pris avec elle**, même non confirmés, classés (Prix à proposer / En attente de la cliente / Confirmés à venir / Passés et annulés) et filtrables par type (sur mesure ou catalogue). Elle y voit la cliente, sa description et ses photos, et propose le prix. Ses propres réservations comme cliente restent dans « Mes rendez-vous ».
* **Photos et vidéos en carrousel :** un clic sur une photo ou l'icône d'agrandissement d'une vidéo ouvre une **visionneuse** plein écran : flèches du clavier, boutons ou glissement du doigt pour faire défiler, Échap pour fermer.
* **Changer la date ou l'heure :** la cliente peut déplacer elle-même un rendez-vous **confirmé** (même coiffeuse, même durée, même prix) depuis la page du rendez-vous. Son horaire actuel est gardé tant que le nouveau n'est pas validé ; la coiffeuse et l'admin sont avertis.
* **Équipe avertie :** quand une cliente réserve, annule ou déplace, l'admin et la coiffeuse concernée reçoivent une alerte du site, et un courriel / SMS selon leurs choix (profil).
* **Listes :** chaque liste (rendez-vous, prestations, produits, coiffeuses, messages, alertes, historique) est paginée (20 par page) et, pour l'administration, filtrable.

### **B. Gestion des Coiffeuses Indépendantes**
* **Modèle financier :** Calcul en temps réel des commissions ou des loyers de chaise selon la grille tarifaire de **KYJA BEAUTÉ**[cite: 1], à partir du **prix final** saisi au salon (et non du prix estimatif).
* **Confidentialité :** Les preuves de compétences (diplômes/expérience) sont réservées exclusivement à la validation administrative de **KYJA BEAUTÉ**[cite: 1].

### **C0. Gestion du catalogue par l'administrateur**
* **Catégories :** l'admin crée, renomme et supprime des catégories de prestations ; elles apparaissent en **liste déroulante** dans le formulaire de prestation et comme filtres sur le site. Un nom de catégorie est unique (sans tenir compte des majuscules).
* **Supprimer ne doit jamais effacer l'historique :**
  * une prestation **jamais réservée** est supprimée définitivement (photos et vidéos comprises) ;
  * une prestation **déjà réservée** est **archivée** : invisible partout, mais les rendez-vous passés la gardent ; restaurable ;
  * une **coiffeuse** « supprimée » est **archivée** : elle disparaît de la réservation et des listes, son compte ne peut plus se connecter, mais son **historique et ses coordonnées sont conservés** ; restaurable ;
  * s'il reste des **rendez-vous à venir**, la suppression est refusée (pour une coiffeuse, l'admin peut choisir de les annuler explicitement) ;
  * la prestation intégrée « Demande sur mesure » ne peut pas être supprimée.
* **Supprimer une catégorie** supprime aussi toutes ses prestations. L'admin voit un **avertissement** listant ce qui sera effacé et ce qui sera archivé, se voit **proposer une sauvegarde téléchargeable** (ZIP : prestations en JSON + photos et vidéos) avant de continuer, et doit écrire le nom de la catégorie pour confirmer.
* **Stock faible :** sur la fiche d'un produit, quand il reste **5 unités ou moins**, le nombre restant s'affiche à côté de « En stock », entre parenthèses et **en rouge** (ex. « En stock (3) »). À 0 : « Rupture de stock ».

### **C. Logistique et Inventaire**
* **Livraison :** Notifications d'étapes ("En route", "Arrivé") avec preuve photographique obligatoire pour garantir la réception des produits[cite: 1].
* **Inventaire Hybride :** Rappel automatique de mise à jour après chaque vente au comptoir pour éviter les ruptures de stock en ligne[cite: 1].

---

## 5. Conformité Légale (Canada)
* **Consentement :** Opt-in explicite requis pour les communications marketing de **KYJA BEAUTÉ**[cite: 1].
* **Protection :** Accès direct pour la cliente à la suppression totale de ses données personnelles[cite: 1], depuis son profil (`/clients/me`) : **suppression de son identité par anonymisation**, avec mot de passe et confirmation. Sont effacés : nom, email, téléphone, adresse, photos d'inspiration, descriptions de ses demandes et sessions ; ses rendez-vous à venir sont annulés. Sont conservés **sans l'identifier** : l'historique des rendez-vous passés, les montants payés (7 ans, obligation fiscale) et les preuves de consentement (obligation légale).
* **Accès :** la cliente peut télécharger **une copie de toutes ses données** (fichier JSON) depuis son profil, et consulter son historique de rendez-vous.
* **Profil :** modification du nom, du téléphone et de l'adresse ; changement de mot de passe (les autres appareils sont déconnectés) ; choix des communications marketing modifiable à tout moment, avec historique conservé.
