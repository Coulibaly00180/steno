# Connaissance : personnes et dates, recherche hybride, conversations, modèles

| | |
|---|---|
| Statut | Implémenté (2026-09-24) |
| Origine | Feuille de route n° 2 : points 16, 17, 18 et 20 |
| Migration | `0011_entities_searches_feedback` |

## n°16 — Personnes, organisations, lieux, dates

- **Extraction** (`app/entities.py`, `llm.extract_entities`) : le LLM lit chaque bloc de transcription (12 000 caractères) et renvoie une liste JSON imposée par un schéma (paramètre `format` d'Ollama) : nom, type, horodatage de la première apparition dans le bloc.
- **Vérifications**, car le modèle se trompe :
  - l'horodatage doit tomber dans le bloc ;
  - le nom doit y figurer, en mot entier, sans accents ni casse ;
  - une date doit ressembler à une date (mois, jour, année, repère relatif) ;
  - rien ne doit commencer par un nombre ni porter une unité (euros, %, fps, Hz, pouces…) ;
  - les étiquettes « Intervenant N » sont écartées.
  
  Chaque mention est recalée sur la ligne qui nomme vraiment l'entité (à 30 s près). Une réponse coupée par la limite de jetons garde ses éléments complets.
- **Mesuré sur une vidéo réelle** (comparatif de PC portables, 11 min) :
  - première version : 40 fiches, dont des prix et des tailles d'écran classés comme dates ou lieux, et « Intervenant 1 » comme personne ;
  - après les vérifications : 18 fiches pertinentes (marques, gammes, cartes graphiques, Black Friday, septembre 2026).
  
  Un bloc dont la réponse avait dépassé la limite de jetons était perdu en entier ; il est maintenant récupéré.
- **Regroupement** : un même nom se rejoint d'une vidéo à l'autre (clé sans accents ni casse, titres « M. », « Dr »… retirés pour les personnes). L'utilisateur peut renommer (le nom affiché ; la clé ne change pas), masquer, ou fusionner deux fiches : la fiche fusionnée garde un renvoi (`merged_into`), et les prochaines extractions de cette graphie vont à la cible.
- **Tâche de fond** `ENTITIES`, sur la file secondaire comme l'indexation : elle passe après les analyses et ne les retarde pas. Elle est lancée après chaque analyse, identification d'intervenants ou correction de transcription (l'état passe à `STALE`). Au démarrage du worker, les vidéos traitées sans relevé sont rattrapées. Une tâche de fond n'est jamais « le traitement de la vidéo » dans l'interface (`BACKGROUND_KINDS`).
- **Interface** :
  - page « Personnes et dates » : filtres par type, recherche, fiches masquées, état du relevé ;
  - fiche « Tout ce qui en est dit » : mentions horodatées vidéo par vidéo, chacune ouvrant la vidéo à ce moment ;
  - boutons « Poser une question » (`/ask?entity=`, sur les seules vidéos qui citent la fiche) et « Dans la bibliothèque » (`/videos?entity=`) ;
  - puces sur la page vidéo.

## n°17 — Recherche hybride et collections

- `GET /videos?q=` fusionne deux classements par rang réciproque (RRF, k = 60) :
  - la recherche plein texte (tous les mots) ;
  - le passage le plus proche de chaque vidéo par le sens (bge-m3), gardé s'il est à une distance cosinus d'au plus 0,52.
  
  Une vidéo trouvée seulement par le sens affiche ce passage, marqué « par le sens » ; sinon l'extrait habituel surligne les mots.
- **Seuil mesuré** sur la bibliothèque :

  | Requêtes | Distance |
  |---|---|
  | Sur le sujet (« ordinateur portable pour jouer », « quelle carte graphique choisir ») | 0,37 à 0,47 |
  | « prix et promotions » | 0,52 |
  | Hors sujet (« réunion budget », « météo », « football », « cuisine ») | 0,56 à 0,72 |

  Exemple : « machine pour les jeux vidéo » ne trouve rien en mots exacts, mais la recherche hybride trouve la vidéo.
- **Repli** : `mode=exact` force les mots seuls ; si les embeddings ne répondent pas, la recherche revient aux mots. L'en-tête `X-Search-Mode` indique laquelle a tourné.
- **Collections** (`/library/searches`) : les filtres de la bibliothèque (recherche, statut, langue, période, tag, fiche) sont enregistrés sous un nom et rappelés d'un clic. Les filtres sont aussi dans l'adresse de la page : un rechargement ou un lien les conserve.

## n°18 — Conversations

- **Renommer** (`PATCH /library/conversations/{id}`), **rechercher** dans les titres et les messages (`?q=`), **filtrer** celles qui ont une réponse signalée (`?flagged=true`, pastille rouge dans l'historique).
- **Pouce** haut ou bas sur chaque réponse, dans les conversations comme dans le chat d'une vidéo (`feedback` : 1, -1 ou vide ; un second clic retire la note). L'événement `done` du flux renvoie l'identifiant de la réponse.
- **Export Markdown** d'une conversation (questions, réponses, sources avec titre et horodatage, mentions « interrompue » et « signalée ») et du chat d'une vidéo.

## n°20 — Page « Modèles »

- **Carte graphique** : nom, mémoire utilisée sur le total, utilisation, modèles chargés dans Ollama et leur part en mémoire vidéo. Le service `live` (qui tourne sur le GPU) publie `nvidia-smi` toutes les 10 s dans Redis (`steno:gpu`).
- **Modèle de langage** :
  - modèles Ollama installés (taille, paramètres, quantification) ;
  - « Utiliser », « Tester » (chargement, jetons/s en écriture et en lecture, durée estimée d'un résumé standard), « Supprimer » (refusé pour le modèle utilisé, celui par défaut et le modèle d'embeddings) ;
  - téléchargement avec progression (`/api/pull` d'Ollama relayé en SSE), depuis une liste de modèles suggérés avec leur besoin en mémoire, ou par nom.
- **Modèle de transcription** :
  - choix parmi les tailles de faster-whisper, avec celles déjà téléchargées ;
  - « Tester » transcrit la première minute de la vidéo la plus récente, dans le service `live` (le GPU, sans passer derrière la file d'analyse), après une passe d'échauffement : la première passe paie l'initialisation CUDA (17 s pour une minute qui prend ensuite 1,7 s).
- **Portée** : le choix (`app_settings`, section `models`) s'applique aux prochains traitements. Le LLM est lu à chaque appel, avec 5 s de cache ; le modèle Whisper l'est au début de chaque tâche. Le modèle d'embeddings et le modèle de la transcription en direct restent fixés par l'environnement.
- **Défaut corrigé** : la surcouche GPU ne donnait `WHISPER_MODEL=large-v3-turbo` qu'au worker ; l'API annonçait « small ». Elle le reçoit aussi. La carte « Intelligence artificielle » des Paramètres, qui affichait des valeurs figées, renvoie vers cette page.

## Vérifications

- 373 tests backend (39 nouveaux) : vérifications et recalage des entités, fusion qui survit à une nouvelle extraction, réponse JSON coupée, tâche et rattrapage ; recherche hybride (sens seul, mots, seuil, repli), collections ; renommage, recherche, filtre, pouces, exports ; page Modèles avec un faux Ollama, test Whisper de bout en bout avec un vrai décodage ffmpeg.
- Sur la vraie pile GPU (données de test supprimées ensuite) :
  - rattrapage des entités de la bibliothèque (18 s pour 11 min) ;
  - recherches réelles et mesure du seuil ;
  - qwen3:8b à 141 jetons/s ; large-v3-turbo à ×35 temps réel ; VRAM de la RTX 5080 affichée ;
  - question limitée aux vidéos citant « Amazon », réponse signalée, renommée, filtrée, exportée.

## Limites

- Les produits d'une marque (« MSI Katana ») sont classés comme organisations, faute d'un type « produit ».
- Le seuil de la recherche par le sens est mesuré sur une bibliothèque d'une vidéo : à revoir quand elle grandira.
- Une erreur de transcription produit une fiche à part (« Léneau Volok » pour « Lenovo LOQ ») : à fusionner à la main, ou à corriger dans la transcription (le glossaire qui apprend la retiendra).
- Changer de modèle de langage en cours de résumé : les appels suivants utilisent le nouveau modèle.
