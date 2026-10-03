# Envoyer à Sténo depuis le navigateur

| | |
|---|---|
| Statut | Livré (2026-10-03) |
| Origine | Feuille de route n° 4 : phase 1 |
| Migration | `0017_access_tokens` |

## Ce qui change pour l'utilisateur

On envoie à Sténo la vidéo ou le podcast ouvert dans le navigateur sans copier de lien, de trois façons :

- **l'extension** Chrome, Edge ou Firefox (`extension/`, guide dans `extension/README.md`) :
  - un clic sur l'icône (ou Alt+Maj+S) met l'onglet en file, avec les options par défaut de l'extension ;
  - un clic droit sur l'icône ou la page › « Envoyer à Sténo avec des options… » ouvre une petite fenêtre (template, intervenants) puis « Envoyer » ;
  - une notification confirme l'envoi, puis « Analyse terminée » (ou l'échec) ; un clic ouvre la vidéo ;
- **le favori « Envoyer à Sténo »**, à glisser dans la barre des favoris depuis Paramètres › Envoyer à Sténo : il ouvre la page `/envoyer` de Sténo avec le lien, prêt à analyser ;
- **le téléphone Android** : Sténo, ouvert par l'adresse HTTPS du réseau local, s'installe comme application (« Ajouter à l'écran d'accueil »). Il apparaît alors dans « Partager » : YouTube › Partager › Sténo ouvre `/envoyer`.

Dans Paramètres › Accès et sécurité, des **jetons d'accès** se créent (affichés une seule fois) et se révoquent. L'extension et les scripts les présentent à la place du mot de passe.

## Choix

### Jetons d'accès

- **Format** : `steno_` suivi de 32 octets aléatoires (`secrets.token_urlsafe`). Seul le SHA-256 est enregistré (table `access_tokens`), avec un préfixe de 10 caractères pour les reconnaître dans la liste. Un hachage lent (scrypt, comme le mot de passe) n'apporte rien sur 256 bits aléatoires.
- **Présentation** : en-tête `Authorization: Bearer …`, jamais dans l'adresse ni dans un cookie.
- **Vérification** (`AccessGuard`, règle pure `auth.decide`) :
  - le jeton est cherché en base à **chaque** requête qui en porte un, sans cache : un jeton révoqué (ligne supprimée) est refusé dès la requête suivante. Seules ces requêtes coûtent une lecture indexée ; la dernière utilisation est écrite au plus une fois par minute ;
  - un jeton présenté doit être valide, même là où la requête passerait sans (sur l'ordinateur, sans mot de passe) : l'extension apprend ainsi qu'un jeton a été révoqué, au lieu de fonctionner jusqu'au jour où elle sort du poste ;
  - un jeton **n'ouvre pas le réseau** : sans mot de passe, les requêtes du réseau restent refusées (403), comme avant ;
  - un jeton ne permet **ni de gérer les jetons** (`/access/tokens` refuse toute requête qui en porte un), **ni de changer le mot de passe** (depuis le réseau, `/auth/password` demande toujours le mot de passe actuel). Un jeton volé ne se reproduit pas ;
  - **changer ou supprimer le mot de passe révoque tous les jetons**, comme il ferme toutes les sessions : un jeton créé depuis une session volée ne survit pas à la remise à zéro.
- **Portée**, choisie à la création :
  - « **Envoyer des liens** » (par défaut, pour l'extension) : cinq routes seulement (`auth.IMPORT_ROUTES`) : `GET /templates`, `GET /settings/url-import`, `POST /imports/url/preview`, `POST /imports/url`, `GET /jobs/{id}`. Ailleurs : 403. Un jeton d'extension volé ne lit ni n'efface la bibliothèque ;
  - « **Accès complet** » (scripts, futures intégrations) : toute l'API, comme une session.
- 50 jetons au plus.

### Extension

- **Manifest V3, un seul manifeste** pour les trois navigateurs : `background.service_worker` pour Chrome et Edge, `background.scripts` pour Firefox, qui ignore l'autre clé (et inversement). Firefox 128 au minimum, pour `optional_host_permissions`.
- **Permissions minimales** : `activeTab` (l'adresse de l'onglet au clic, sans `tabs`), `contextMenus`, `notifications`, `alarms` (suivi des analyses), `storage`. **Aucune** autorisation d'hôte à l'installation : seule l'adresse réglée dans les options est demandée, au clic sur « Enregistrer et tester ». Changer d'adresse retire l'autorisation précédente.
- **Une seule porte de sortie** : toutes les requêtes passent par `stenoFetch` (`lib.js`), qui n'accepte qu'un chemin `/api/…` sur l'origine réglée, sans cookie (`credentials: "omit"`) et sans suivre de redirection (`redirect: "error"`). Un test vérifie qu'aucun autre fichier n'appelle `fetch` ou équivalent. Le jeton ne part jamais en HTTP clair hors de l'ordinateur (`127.0.0.1`, `localhost`) : ailleurs, l'adresse HTTPS est exigée.
- **Rien dans les pages** : pas de script injecté, pas de lecture du contenu. Le titre vient de l'aperçu de Sténo (yt-dlp), sinon de l'onglet, nettoyé (« (3) … - YouTube »).
- **Aperçu avant la mise en file** : l'extension appelle `/imports/url/preview` puis `/imports/url`. Une page refusée (option YouTube désactivée, adresse privée) échoue tout de suite avec le message de Sténo, au lieu d'échouer plus tard dans le worker. Un flux de podcast ouvre `/envoyer` pour choisir les épisodes.
- **Les droits** : la confirmation « J'enverrai seulement des contenus que j'ai le droit d'utiliser » est cochée une fois dans les options ; sans elle, rien n'est envoyé.
- **Deux clics avec options** : l'extension choisit le clic droit (menu « avec des options… ») plutôt qu'une fenêtre ouverte à chaque clic, pour garder l'envoi en **un** clic.

### Favori et partage Android : la page `/envoyer`

- Une seule page sert le favori et la cible de partage (`share_target` en GET dans `app/manifest.ts`). Android met souvent le lien dans `text`, entouré de mots : `sharedLink` prend le premier lien http(s).
- **La mise en file demande un clic** sur « Analyser », avec la case des droits. Sans ce clic, n'importe quel site pourrait lancer des analyses en ouvrant `http://127.0.0.1:3000/envoyer?url=…`.
- L'aperçu du lien se lance seul quand la page arrive **sans site référent** (le favori ouvre Sténo en `noreferrer`, le partage Android n'en a pas) ; envoyé par un autre site, Sténo attend « Vérifier le lien » avant de contacter l'adresse.
- React 19 refuse les liens `javascript:` dans le JSX : l'adresse du favori est posée sur l'élément après l'affichage.
- L'application installable ne demande pas de service worker (Chrome ne l'exige plus pour l'installation) ; il faut le HTTPS du proxy et l'autorité locale installée sur le téléphone.

## Revue de sécurité (agent security-reviewer, 2026-10-03)

Aucun constat critique ni élevé ; les deux critères de sortie tenaient déjà. Les constats moyens et faibles ont été corrigés avant la livraison :

| Constat | Correction |
|---|---|
| Moyen : `/envoyer` et les boutons des Paramètres pouvaient être affichés dans un cadre invisible d'un autre site (clickjacking) | `Content-Security-Policy: frame-ancestors 'none'` et `X-Frame-Options: DENY` sur toutes les pages (`next.config.ts`) |
| Moyen : changer le mot de passe ne révoquait pas les jetons | tous les jetons révoqués au changement ou à la suppression du mot de passe |
| Moyen : un jeton donnait accès à toute l'API | portée « Envoyer des liens » par défaut (5 routes) ; « Accès complet » sur demande |
| Moyen (existant, aggravé par les jetons) : **DNS rebinding**, une page d'un domaine public pointé vers 127.0.0.1 passait pour « cet ordinateur » et pouvait créer un jeton | les noms de domaine publics sont refusés (421) : middleware Next pour les pages, `AccessGuard` pour l'API (en-têtes `Host` et `X-Forwarded-Host`). Acceptés : adresses IP, `localhost`, noms sans point (`web`, nom de l'ordinateur), suffixes locaux (`.local`, `.lan`, `.home`, `.home.arpa`, `.internal`). Règle dans `auth.host_allowed`, en miroir dans `frontend/lib/hosts.ts`. Le middleware ne couvre pas `/api` : il mettrait en mémoire les envois de 2 Go |
| Faible : un autre site ouvrant `/envoyer?url=…` faisait contacter l'adresse par Sténo | aperçu automatique seulement sans site référent |
| Faible : jeton envoyé en HTTP clair vers une adresse du réseau | refusé par l'extension |
| Faible : lecture différente d'un en-tête répété entre `AccessGuard` et les routes | première occurrence partout |
| Faible : le favori s'exécute dans la page visitée, qui peut détourner `window.open` et apprendre l'adresse de Sténo | limite propre aux favoris ; l'extension est recommandée (Paramètres › Envoyer à Sténo) |

Reste connu, hors de ce lot : la vérification d'une adresse importée (`url_import.check_url`) résout le nom, puis httpx le résout à nouveau ; un rebinding entre les deux viserait une adresse interne en GET aveugle. Épingler l'adresse résolue dans le transport httpx est une piste pour plus tard.

## Vérifié (2026-10-03, pile GPU)

| Vérification | Résultat |
|---|---|
| Extension réelle chargée dans Chromium (Playwright, conteneur sur le réseau de la pile), page YouTube de 19 s, option YouTube **désactivée** | refus immédiat, notification avec le message de Sténo |
| Même envoi, option YouTube activée le temps du test | en file en un appel (`send`), titre « Me at the zoo » lu par yt-dlp ; analyse terminée en ~35 s ; notification « Analyse terminée » au passage suivant de l'alarme |
| Adresses contactées par l'extension pendant ces essais | une seule : l'adresse configurée |
| Jeton révoqué | `GET /api/tags` : 200 avant, 401 « Jeton d'accès invalide ou révoqué » à la requête suivante ; l'extension affiche « Jeton d'accès refusé (révoqué ?) » |
| `/envoyer?text=Regardez ça https://www.youtube.com/watch?v=…` | lien extrait du texte, aperçu lancé, analyse en attente du clic |
| Tests | backend 591 réussis ; extension 9 et noms d'hôte 1 (`node --test`) ; frontend `tsc` et `next build` |
| Après la revue | en-têtes anti-cadre présents ; `Host: attacker.example` refusé (421) sur une page, sur l'API par Next et sur l'API directe ; jeton « Envoyer des liens » : `/templates` 200, `/tags` 403 |

Le clic sur l'icône de la barre d'outils, la boîte de dialogue d'autorisation et le partage depuis un vrai téléphone Android ne s'automatisent pas : ils restent à essayer à la main (voir « Limites »).

## Limites

- **Pas de publication sur les magasins** : chargement « non empaqueté » dans Chrome et Edge (le navigateur le signale au démarrage), module temporaire dans Firefox. Publier demande un compte développeur (Chrome Web Store, Edge Add-ons) ou une signature Mozilla (AMO, distribution non listée possible).
- **Adresse et certificat** : depuis un autre ordinateur ou le téléphone, l'autorité locale de Sténo doit être installée, sinon le navigateur refuse la connexion (l'extension l'explique dans son message d'erreur).
- **Partage Android seulement** : iOS n'offre pas de cible de partage aux applications web. Sur iPhone, le favori reste possible dans Safari.
- **Suivi des analyses** : l'extension interroge Sténo une fois par minute tant qu'une analyse envoyée par elle est en cours, 48 h au plus.

## Fichiers

| Sujet | Fichier |
|---|---|
| Jetons : règle, portée, vérification, création | `backend/app/auth.py` (`decide`, `IMPORT_ROUTES`, `check_access_token`, `new_access_token`), `AccessGuard` (`main.py`), routes `/access/tokens` (`backend/app/routes/access.py`), table `access_tokens` |
| Noms d'hôte refusés (DNS rebinding), cadres interdits | `auth.host_allowed`, `frontend/lib/hosts.ts`, `frontend/middleware.ts`, `headers()` de `frontend/next.config.ts` ; tests `frontend/tests/hosts.test.mjs` |
| Interface des jetons | `frontend/components/AccessTokens.tsx` (dans `AccessPanel.tsx`) |
| Extension | `extension/` (`lib.js`, `background.js`, `options.*`, `send.*`, `manifest.json`), tests `extension/tests/lib.test.js` |
| Favori, partage, application installable | `frontend/app/envoyer/page.tsx`, `frontend/app/manifest.ts`, `frontend/public/icons/`, `frontend/components/SendToStenoPanel.tsx`, `sharedLink` et `bookmarklet` (`frontend/lib/access.ts`) |
| Tests | `backend/tests/test_send_to_steno.py` |
