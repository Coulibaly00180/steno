# Spécification fonctionnelle — Phase 1 : « Des résultats justes et utiles »

| | |
|---|---|
| Statut | Implémenté (2026-09-22), **calibrage R-1 partiel** (2026-09-23, sans le corpus de référence) — voir « Écarts d'implémentation » et « Calibrage partiel » en fin de document |
| Date | 2026-09-22 |
| Périmètre | Évolutions n°5, 7, 11 (dont glossaire global), 12, 3, 15 de la roadmap |
| Prérequis | Phase 0 livrée **avant** le début de la phase 1 : migrations de schéma (Alembic), tri des résumés par `created_at` |
| Estimation | ~2,5 semaines, 1 développeur (dont ~2 j pour le glossaire global et ~1 j de calibrage) |

---

## 1. Contexte

Sténo transcrit (faster-whisper), traduit et résume (Ollama, `qwen3:8b`) des vidéos et fichiers audio jusqu'à 6 h, entièrement en local. Le pipeline (`backend/app/worker.py`) enchaîne : extraction audio → transcription → traduction optionnelle → résumés par blocs de 12 000 caractères → résumé final guidé par un template → exports.

L'analyse du code a mis en évidence des écarts entre ce que l'interface promet et ce que le produit fait :

| Constat | Emplacement | Effet pour l'utilisateur |
|---|---|---|
| Le résumé final est limité à « moins de 250 mots » et 300 tokens, quelle que soit la durée | `backend/app/llm.py:61` | Une vidéo de 3 h tient en un paragraphe |
| `final_summary` ne garde que les lignes de titre (`#`) du template ; le reste du texte est ignoré | `backend/app/llm.py:57` | Les consignes écrites sous chaque rubrique d'un template n'ont aucun effet |
| Les « Instructions supplémentaires » **remplacent** le template ; comme elles n'ont en général aucun titre `#`, les rubriques par défaut sont utilisées et les instructions sont ignorées | `backend/app/worker.py:325` | Le champ « Ex. : concentre le résumé sur les décisions… » ne fait rien |
| Le chat répond toujours en français | `backend/app/llm.py:80` | Réponse en français à une question posée en anglais |
| `num_ctx` n'est pas envoyé à Ollama | `backend/app/llm.py` (`chat`) | La fenêtre de contexte effective dépend du défaut d'Ollama et peut être inférieure au texte envoyé (chat jusqu'à 30 000 caractères, résumé final de longue vidéo) → troncature silencieuse. **À vérifier** sur la version d'Ollama déployée |
| Aucune option de langue source ni de vocabulaire pour Whisper | `worker.py:261` | Noms propres, sigles et jargon mal transcrits, erreurs propagées au résumé |
| Templates : création seulement, pas de modification ni suppression | `main.py` (`/templates`) | Une faute dans un template est définitive |
| Transcription affichée en bloc de texte sans recherche | `videos/[id]/page.tsx` | Impossible de retrouver un passage dans 6 h de texte |
| Aucune alerte de fin de traitement | — | L'utilisateur doit surveiller l'onglet pendant des dizaines de minutes |

## 2. Problème

> « Le résumé ne reflète pas vraiment ma vidéo, et je ne peux pas l'orienter. »

Pour les vidéos longues, le résultat est trop court. Les moyens de le personnaliser (templates, instructions) sont en grande partie sans effet, et la qualité de la transcription se dégrade dès que le vocabulaire est spécialisé.

## 3. Objectifs

1. La longueur du résumé suit la durée du contenu et le choix de l'utilisateur.
2. Le contenu complet d'un template et les instructions supplémentaires sont effectivement appliqués.
3. Le chat répond dans la langue de la question.
4. L'utilisateur peut améliorer la transcription (langue source, vocabulaire propre à la vidéo, glossaire global saisi une seule fois).
5. L'utilisateur gère entièrement ses templates.
6. L'utilisateur retrouve un passage dans la transcription et sait quand le traitement est terminé sans surveiller l'écran.

## 4. Non-objectifs

- Régénérer un résumé sans retranscrire (n°9, phase 2).
- Modifier la transcription ou le résumé (n°10, phase 2).
- Lecteur vidéo et timestamps cliquables (n°1–2, phase 2).
- Recherche sur plusieurs vidéos (n°17, phase 3).
- Traduire l'interface elle-même : l'interface reste en français.
- Notifications quand aucun onglet de l'application n'est ouvert (push, service worker).
- Plusieurs glossaires nommés (par client, par projet) ou glossaire lié à un template (voir Questions ouvertes).
- Rendre dynamiques les autres sections de la page Paramètres (modèle LLM, limites…) : seule la section Glossaire devient modifiable.

## 5. Acteurs

- **Utilisateur local** : seul acteur. Pas d'authentification, application exposée sur `127.0.0.1`.
- **Profils types** : responsable qui résume des réunions (surtout des décisions et actions), formateur ou étudiant (cours longs, vocabulaire technique), créateur ou veilleur (podcasts, interviews en anglais).

## 6. User stories

| ID | En tant que… | je veux… | afin de… | Évol. |
|---|---|---|---|---|
| US-1 | utilisateur | choisir un résumé court, standard ou détaillé | obtenir un niveau de détail adapté à mon usage | 5 |
| US-2 | utilisateur d'une vidéo longue | que le résumé standard grandisse avec la durée | que la 3ᵉ heure ne disparaisse pas | 5 |
| US-3 | utilisateur | que mes instructions supplémentaires s'ajoutent au template | orienter le résumé sans perdre sa structure | 5/12 |
| US-4 | utilisateur | que les consignes écrites sous chaque rubrique de mon template soient suivies | obtenir exactement le format voulu | 12 |
| US-5 | utilisateur | poser une question en anglais et recevoir une réponse en anglais | utiliser le chat dans ma langue | 7 |
| US-6 | utilisateur | forcer la langue source | éviter une mauvaise détection (accent, intro musicale) | 11 |
| US-7 | utilisateur | fournir une liste de noms et de termes propres à une vidéo | que la transcription et le résumé les écrivent correctement | 11 |
| US-7b | utilisateur récurrent | enregistrer une fois pour toutes les noms de mon équipe, de mes clients et mon jargon | ne pas les ressaisir à chaque import | 11 |
| US-7c | utilisateur | désactiver le glossaire global pour un import | éviter qu'il perturbe une vidéo sans rapport (podcast perso, autre langue) | 11 |
| US-8 | utilisateur | modifier, dupliquer et supprimer un template | corriger et faire évoluer mes formats | 12 |
| US-9 | utilisateur | choisir mon template par défaut | ne pas le re-sélectionner à chaque import | 12 |
| US-10 | nouvel utilisateur | disposer de templates prêts à l'emploi | obtenir un bon résultat dès la première vidéo | 12 |
| US-11 | utilisateur | rechercher un mot dans la transcription et passer d'un résultat à l'autre | retrouver un passage précis | 3 |
| US-12 | utilisateur | être prévenu à la fin du traitement | faire autre chose pendant ce temps | 15 |

## 7. Parcours principal

1. Sur « Analyser une vidéo », l'utilisateur dépose un fichier.
2. Il choisit (options repliées sous « Options avancées », sauf la longueur) :
   - langue de traduction (existant) ;
   - template (présélectionné : le template par défaut) ;
   - **longueur du résumé** : Court / Standard (défaut) / Détaillé, avec une indication du nombre de mots attendu ;
   - **langue parlée** : Détection automatique (défaut) ou une langue ;
   - **vocabulaire de cette vidéo** : termes séparés par des virgules ou des retours à la ligne ;
   - **utiliser le glossaire global** : case cochée par défaut, avec « 42 termes · Modifier » (lien vers Paramètres) ;
   - instructions supplémentaires (existant, désormais **ajoutées** au template).
3. Il lance l'analyse et arrive sur la page de la vidéo. La carte de progression propose « Me prévenir à la fin ». Le titre de l'onglet affiche l'avancement.
4. À la fin, une notification apparaît (si elle a été autorisée) et le titre de l'onglet indique « Terminé ».
5. Dans l'onglet Transcription, il recherche un terme et navigue entre les occurrences.
6. Dans le Chat, il pose une question en anglais et reçoit une réponse en anglais.

---

## 8. Exigences fonctionnelles

### 8.1 Longueur du résumé (n°5)

- **F-5.1** L'import accepte un paramètre `summary_length` ∈ {`short`, `standard`, `detailed`}, `standard` par défaut. Une valeur inconnue → 422.
- **F-5.2** Le budget de mots du résumé final est calculé par le backend selon la règle R-1 (§9). Le prompt final indique ce budget (« environ N mots ») au lieu de « moins de 250 mots ».
- **F-5.3** `num_predict` du résumé final = budget en mots × 1,6 (marge pour le français et le Markdown), borné par `LLM_MAX_OUTPUT_TOKENS`. La borne haute de validation de ce réglage passe de 4096 à 8192.
- **F-5.4** Pour `detailed`, les résumés intermédiaires passent de « 6 puces / 240 tokens » à « 10 puces / 400 tokens ». Sinon, pas de changement.
- **F-5.5** **Réduction hiérarchique** : si le texte cumulé des résumés intermédiaires dépasse 50 % de la fenêtre de contexte configurée, les blocs sont d'abord regroupés et résumés par groupes, puis le résumé final est produit à partir de ces groupes. Nouvelle étape de progression `SUMMARIZING_GROUPS` (libellé « Consolidation des blocs »).
- **F-5.6** La requête Ollama envoie `num_ctx` = `LLM_NUM_CTX` (nouveau réglage, défaut 8192) pour **tous** les appels (traduction, résumés, chat).
- **F-5.7** La longueur choisie est affichée dans l'en-tête de l'onglet Résumé (« Résumé standard · ~600 mots ») et enregistrée dans `metadata.json`.
- **F-5.8** « Réessayer » reprend la même longueur.

### 8.2 Application réelle du template et des instructions (transverse n°5/12)

- **F-T.1** Le prompt final contient **le texte intégral du template** : rubriques et consignes. La liste des rubriques extraites (`#`, `##`) sert à exiger l'ordre et la présence de chaque rubrique.
- **F-T.2** Les instructions supplémentaires sont **ajoutées** au template, dans une section distincte du prompt (« Consignes complémentaires de l'utilisateur »). Elles ne remplacent plus le template.
- **F-T.3** Priorité en cas de conflit : instructions supplémentaires > template > règles par défaut. La règle de non-invention (prompt système) prime sur tout.
- **F-T.4** Le template et les instructions sont placés dans des balises délimitées et le prompt demande d'ignorer toute consigne présente **dans la transcription** (même logique de protection que le chat).
- **F-T.5** Un template sans aucune rubrique `#` reste valide : le LLM suit alors ses consignes en texte libre, sans rubriques imposées.

### 8.3 Langue du chat (n°7)

- **F-7.1** La réponse est rédigée dans la langue de la question.
- **F-7.2** Si la langue de la question est ambiguë (moins de 3 mots, ou uniquement des noms propres ou des chiffres), on utilise, dans l'ordre, la langue du dernier message utilisateur non ambigu, puis la langue cible de traduction de la vidéo, puis la langue détectée, puis le français.
- **F-7.3** La phrase de refus « Ce n'est pas mentionné dans la vidéo » est donnée dans la langue de la réponse.
- **F-7.4** Les citations exactes de la transcription peuvent rester dans la langue d'origine, suivies d'une traduction entre parenthèses.
- **F-7.5** Implémentation par consigne dans le prompt, sans bibliothèque de détection de langue. La résolution de l'ambiguïté (F-7.2) est calculée côté backend et transmise comme « langue de repli » dans le prompt.

### 8.4 Langue source, vocabulaire et glossaire global (n°11)

- **F-11.1** L'import accepte `source_language` : code ISO 639-1 parmi une liste proposée (fr, en, es, de, it, pt, nl, ar, zh, ja…) ou vide pour la détection automatique. Code non pris en charge → 422.
- **F-11.2** Si `source_language` est fourni, il est transmis à Whisper (`language=`) et enregistré comme `detected_language`, avec l'indicateur `source_language_forced = true`.
- **F-11.3** L'import accepte `vocabulary` : texte libre de 1 000 caractères maximum, découpé en termes sur les virgules, points-virgules et retours à la ligne. Termes vides ignorés, doublons retirés en conservant l'ordre, 100 termes maximum, 60 caractères maximum par terme.
- **F-11.4** Le **vocabulaire effectif** (F-11.12) est transmis à Whisper comme `initial_prompt` (« Termes : A, B, C. »). La phase technique évaluera aussi `hotwords` (faster-whisper) et choisira l'option qui donne le meilleur résultat sur le corpus de test.
- **F-11.5** Le vocabulaire effectif est aussi injecté dans les prompts de traduction et de résumé comme « orthographe de référence des noms propres et termes techniques ».
- **F-11.6** Le formulaire préremplit la langue source avec la valeur du dernier import (stockage local du navigateur). Le vocabulaire de la vidéo n'est **pas** prérempli : les termes récurrents vont dans le glossaire global.
- **F-11.7** « Réessayer » reprend la langue source et le vocabulaire effectif **tel qu'il était au moment de l'import** (instantané, F-11.13), même si le glossaire global a changé depuis.
- **F-11.8** La page de la vidéo affiche la langue avec la mention « (forcée) » ou « (détectée) », ainsi que le vocabulaire utilisé : « 12 termes (4 de la vidéo, 8 du glossaire) », détail au survol.

#### Glossaire global

- **F-11.9** La page Paramètres comporte une section **Glossaire global** modifiable : une zone de texte, un terme par ligne (virgules et points-virgules acceptés, puis normalisés en un terme par ligne à l'enregistrement), avec un bouton Enregistrer et le compteur « N / 300 termes ».
- **F-11.10** Normalisation et limites : espaces de début et de fin retirés, lignes vides ignorées, doublons retirés sans tenir compte de la casse (la première écriture est conservée), 300 termes maximum, 60 caractères maximum par terme. Au-delà → 422 avec la limite dépassée et, le cas échéant, le terme fautif.
- **F-11.11** L'import accepte `use_global_glossary` (booléen, `true` par défaut). Le formulaire affiche une case cochée par défaut, avec le nombre de termes du glossaire et un lien vers Paramètres. Si le glossaire est vide, la case est remplacée par « Aucun glossaire global · Créer ».
- **F-11.12** **Vocabulaire effectif** = termes de la vidéo, puis termes du glossaire (si activé), doublons retirés sans tenir compte de la casse, **les termes de la vidéo étant prioritaires**. Règle de transmission en R-9.
- **F-11.13** Au moment de l'import, le vocabulaire effectif est **figé** sur la vidéo (instantané). Modifier ou vider le glossaire ensuite ne change ni les vidéos déjà traitées, ni les traitements en attente, ni une relance.
- **F-11.14** Enregistrer le glossaire ne relance aucun traitement. Un message précise : « Les modifications s'appliquent aux prochains imports. »
- **F-11.15** Le glossaire global est inclus dans `metadata.json` de chaque vidéo uniquement sous la forme de l'instantané effectivement utilisé (F-11.13).

### 8.5 Gestion des templates (n°12)

- **F-12.1** Modifier : nom, description, contenu. Validation identique à la création (nom de 1 à 120 caractères, unique ; contenu non vide).
- **F-12.2** Dupliquer : crée « <nom> (copie) », puis « (copie 2) », etc. en cas de conflit. Le nouveau template n'est pas le défaut.
- **F-12.3** Supprimer, avec confirmation. Règles R-4 à R-6.
- **F-12.4** « Définir par défaut » sur n'importe quel template. L'ancien défaut perd le statut de manière atomique.
- **F-12.5** Modèles de départ installés une seule fois (migration de données), puis modifiables et supprimables comme les autres :
  - **Compte-rendu de réunion** (le template actuel « Compte-rendu standard », renommé ; il reste le défaut) ;
  - **Cours / formation** : objectifs, notions clés avec définitions, exemples, points à retenir, questions de révision ;
  - **Podcast / interview** : intervenants et sujet, thèmes abordés, citations marquantes, idées clés, ressources mentionnées ;
  - **Présentation / démo** : problème, solution présentée, fonctionnalités, chiffres cités, questions du public, prochaines étapes.
- **F-12.6** Un template supprimé n'est jamais réinstallé automatiquement. Le code d'amorçage actuel du `lifespan` n'intervient que si **aucun** template n'existe.
- **F-12.7** L'éditeur affiche un aperçu de la structure détectée (liste des rubriques) et une aide : « Les titres `#` deviennent les rubriques du résumé ; le texte sous chaque titre est une consigne pour cette rubrique. »

### 8.6 Recherche dans la transcription (n°3)

- **F-3.1** Un champ de recherche apparaît en haut des onglets Transcription et Traduction.
- **F-3.2** La recherche ne tient compte ni de la casse ni des accents (« reunion » trouve « Réunion »). Elle démarre à partir de 2 caractères, avec un délai de 150 ms après la frappe.
- **F-3.3** Toutes les occurrences sont surlignées. L'occurrence active est mise en évidence et amenée à l'écran.
- **F-3.4** Un compteur affiche « 3 / 12 ». Boutons précédent et suivant ; Entrée = suivant, Maj+Entrée = précédent, Échap = effacer. La navigation boucle en fin de liste.
- **F-3.5** La transcription est affichée **segment par segment** (à partir de `segments`), avec le timestamp de chaque segment. Cet affichage servira de base aux timestamps cliquables de la phase 2.
- **F-3.6** Aucun résultat → « Aucun résultat pour « … » ». Le raccourci Ctrl+F du navigateur n'est pas intercepté.
- **F-3.7** Changer d'onglet conserve le terme recherché pour l'onglet Transcription et l'onglet Traduction séparément.

### 8.7 Notification de fin (n°15)

- **F-15.1** Pendant le traitement, le titre de l'onglet affiche « (42 %) <nom du fichier> ». À la fin : « ✓ Terminé · <nom> » ou « ✕ Échec · <nom> ».
- **F-15.2** La carte de progression propose « Me prévenir à la fin ». Au clic, l'autorisation de notification du navigateur est demandée (jamais au chargement de la page).
- **F-15.3** Si l'autorisation est accordée, une notification système est envoyée lors du passage à `COMPLETED` ou `FAILED`, **seulement si l'onglet n'est pas au premier plan**. Un clic sur la notification ramène le focus sur l'onglet.
- **F-15.4** Le choix est mémorisé : quand l'autorisation est déjà accordée, la case « Me prévenir » est cochée par défaut pour les traitements suivants.
- **F-15.5** Si l'API Notification n'est pas disponible ou a été refusée, le bouton est remplacé par une mention : « Notifications bloquées par le navigateur — le titre de l'onglet indiquera la fin. »

---

## 9. Règles métier

- **R-1 — Budget de mots du résumé final** (D = durée en minutes) :

  | Niveau | Formule | Plancher | Plafond |
  |---|---|---|---|
  | Court | 200 + 2 × D | 200 | 350 |
  | Standard | 250 + 8 × max(0, D − 15) | 250 | 1 500 |
  | Détaillé | 2 × Standard | 500 | 2 500 |

  Exemples en Standard : 10 min → 250 mots ; 1 h → 610 mots ; 3 h → 1 500 mots (plafond). **Valeurs provisoires** : elles sont calibrées sur le corpus de référence (§19) avant la clôture de la phase. Les coefficients retenus sont des constantes du backend, documentées dans cette spec après calibrage.
- **R-2** Le budget est indicatif pour le LLM (« environ N mots ») ; `num_predict` est la limite dure. Un résumé coupé en fin de génération (`done_reason = length`) est journalisé et la dernière phrase incomplète est retirée.
- **R-3** Il y a toujours exactement un template par défaut.
- **R-4** Le template par défaut ne peut pas être supprimé → 409 « Définissez un autre template par défaut avant de supprimer celui-ci ».
- **R-5** Un template utilisé par un traitement `QUEUED` ou `RUNNING` ne peut pas être supprimé → 409. Il reste modifiable : le worker lit son contenu au moment de l'étape de résumé final.
- **R-6** Supprimer un template ne touche pas aux résumés existants. Leur `template_id` pointe vers un template qui n'existe plus ; l'interface affiche « Template supprimé ».
- **R-7** Instructions supplémentaires : 2 000 caractères maximum (aujourd'hui aucune limite). Au-delà → 422.
- **R-8** Vocabulaire de la vidéo : limites de F-11.3. Glossaire global : limites de F-11.10. Au-delà → 422, avec un message qui indique la limite dépassée.
- **R-9 — Transmission du vocabulaire effectif** :
  - **Whisper** : son invite est courte (environ 220 tokens utiles). Les termes sont ajoutés dans l'ordre de priorité (vidéo, puis glossaire) jusqu'à 800 caractères ; les suivants ne sont pas transmis à Whisper.
  - **LLM** (traduction, résumés, chat) : tous les termes, jusqu'à 4 000 caractères.
  - Si des termes n'ont pas été transmis à Whisper, la page de la vidéo l'indique : « 35 termes utilisés pour le résumé, dont 20 pour la transcription ». Le glossaire global ne doit donc pas servir de dictionnaire exhaustif : l'aide de la page Paramètres recommande de le limiter aux noms propres et termes vraiment ambigus.
- **R-10** Le glossaire global est unique pour l'installation (pas de profils : un seul utilisateur local).

## 10. États et transitions

Les états du traitement ne changent pas (`QUEUED → RUNNING → COMPLETED | FAILED`). Une étape est ajoutée : `SUMMARIZING_CHUNKS → [SUMMARIZING_GROUPS] → SUMMARIZING_FINAL`. Cette étape n'apparaît que lorsque la réduction hiérarchique (F-5.5) est nécessaire.

Répartition de la barre de progression : blocs 72 → 85, groupes 85 → 90, final 90 → 96. Les libellés d'étape doivent être ajoutés aux deux dictionnaires du frontend (`videos/[id]/page.tsx` et `VideoTable.tsx`).

Template : pas de cycle de vie. L'attribut `is_default` change uniquement via F-12.4.

## 11. Gestion des erreurs

| Cas | Réponse | Message |
|---|---|---|
| `summary_length` invalide | 422 | « Longueur de résumé invalide » |
| `source_language` non pris en charge | 422 | « Langue source non prise en charge » |
| Vocabulaire ou instructions trop longs | 422 | Limite précise indiquée |
| Glossaire global trop long (termes ou caractères) | 422 | Limite dépassée et terme fautif ; le glossaire précédent reste inchangé |
| Glossaire indisponible à l'import (erreur base) | 503 | « Service de données indisponible » — l'import n'est pas créé sans glossaire en silence |
| Nom de template déjà utilisé (modification ou duplication) | 409 | « Un template portant ce nom existe déjà » |
| Suppression du template par défaut | 409 | R-4 |
| Suppression d'un template en cours d'utilisation | 409 | R-5 |
| Template introuvable (modification, suppression, défaut) | 404 | « Template introuvable » |
| Résumé final tronqué par `num_predict` | Pas d'échec | Journalisation + nettoyage (R-2) |
| Réduction hiérarchique : échec d'un appel LLM | Traitement `FAILED` (comportement actuel) | « Échec du traitement vidéo » |

## 12. Cas limites

- **Vidéo très courte** (< 1 min) en Détaillé : plancher de 500 mots, mais le prompt demande de ne pas délayer (« si le contenu est bref, reste bref »). Le budget est un maximum souple, pas un objectif à atteindre.
- **Vidéo sans parole** (transcription vide) : comportement actuel conservé. À vérifier : le pipeline doit produire un résumé « Aucun contenu parlé détecté » plutôt que d'appeler le LLM à vide.
- **Langue forcée incorrecte** (fr forcé sur de l'anglais) : Whisper transcrira mal. Aucune détection de l'erreur en phase 1 ; l'interface indique « (forcée) » pour aider au diagnostic.
- **Vocabulaire contenant des consignes** (« ignore les règles… ») : transmis comme simple liste de termes, entre balises, sans valeur d'instruction.
- **Template modifié pendant qu'un traitement l'utilise** : le worker prend la version présente au moment du résumé final (R-5).
- **Deux onglets ouverts sur la même vidéo** avec notification activée : deux notifications possibles. On utilise le `tag` de notification = id du traitement pour qu'elles se remplacent.
- **Transcription de 6 h** (~6 000 segments) : recherche et surlignage doivent rester fluides (§17).
- **Traduction** : `translated_text` n'est pas découpé en segments. La recherche y fonctionne par lignes, sans timestamps.
- **Question du chat mêlant deux langues** : le LLM choisit ; aucune règle déterministe.
- **Même terme dans la vidéo et dans le glossaire avec une casse différente** (« Okr » / « OKR ») : l'écriture de la vidéo est conservée (F-11.12).
- **Glossaire modifié pendant qu'un traitement est en attente** : sans effet sur ce traitement, qui utilise l'instantané (F-11.13).
- **Glossaire dans une autre langue que la vidéo** : les termes restent transmis ; si cela perturbe la transcription, l'utilisateur décoche la case pour cet import (US-7c).
- **Deux onglets Paramètres modifiant le glossaire** : le dernier enregistrement l'emporte (pas de fusion) ; acceptable pour un utilisateur unique.

## 13. Comportement de l'interface

- **Formulaire d'import** : « Longueur du résumé » visible sous forme de contrôle segmenté à 3 options, avec sous-titre dynamique (« ~610 mots pour cette vidéo ») calculé à partir de la durée lue dans le navigateur. Langue parlée, vocabulaire et instructions sont regroupés sous « Options avancées », repliées par défaut, dépliées si une valeur est préremplie.
- **Page Templates** : chaque carte propose un menu « Modifier · Dupliquer · Définir par défaut · Supprimer ». La modification se fait sur place, dans le formulaire existant (il passe en mode « Modifier le template », avec un bouton Annuler). La suppression passe par la même fenêtre de confirmation que la suppression de vidéo. L'option « Supprimer » est désactivée pour le template par défaut, avec une infobulle qui explique R-4.
- **Page Paramètres** : nouvelle section « Glossaire global » en tête de page (zone de texte, compteur, Enregistrer, message de confirmation). Si le texte a été modifié sans être enregistré, quitter la page demande confirmation. Les autres sections restent en lecture seule.
- **Page vidéo** : barre de recherche dans les onglets Transcription et Traduction ; en-tête du résumé enrichi (F-5.7) ; mention de la langue (F-11.8).
- **Accessibilité** : champ de recherche avec `aria-label` ; compteur dans une zone `aria-live="polite"` ; surlignage via `<mark>`, pas seulement par la couleur ; toutes les actions accessibles au clavier.

## 14. Données

Toutes les modifications passent par une migration Alembic (phase 0).

| Table | Champ | Type | Défaut | Note |
|---|---|---|---|---|
| `processing_jobs` | `summary_length` | varchar(16) | `'standard'` | Repris par « Réessayer » |
| `videos` | `source_language_forced` | boolean | `false` | La valeur elle-même reste dans `detected_language` |
| `videos` | `vocabulary` | text, nullable | — | Termes propres à la vidéo, un par ligne |
| `videos` | `glossary_snapshot` | text, nullable | — | Termes du glossaire global retenus à l'import (F-11.13), un par ligne ; `NULL` si glossaire désactivé |
| `glossary_terms` | `id`, `term`, `position`, `created_at` | nouvelle table | — | Glossaire global ; index unique sur `lower(term)` ; `position` conserve l'ordre saisi |
| `summaries` | `summary_length` | varchar(16), nullable | — | Pour l'affichage F-5.7 |
| `summary_templates` | `updated_at` | timestamptz, nullable | — | |
| `summary_templates` | — | contrainte | — | Index unique partiel `WHERE is_default` → garantit R-3 |

Migration de données : renommer « Compte-rendu standard » en « Compte-rendu de réunion » **seulement si son nom n'a pas été modifié**, puis insérer les 3 autres modèles s'il n'existe aucun template portant ce nom.

**API :**

| Méthode | Route | Changement |
|---|---|---|
| POST | `/videos` | + `summary_length`, `source_language`, `vocabulary`, `use_global_glossary` (formulaire) |
| GET | `/glossary` | Nouveau — `{ "terms": [...] }` dans l'ordre saisi |
| PUT | `/glossary` | Nouveau — remplace la liste entière de façon atomique (transaction unique) |
| PUT | `/templates/{id}` | Nouveau |
| DELETE | `/templates/{id}` | Nouveau |
| POST | `/templates/{id}/duplicate` | Nouveau |
| POST | `/templates/{id}/default` | Nouveau |
| GET | `/videos/{id}` | + `source_language_forced`, `vocabulary`, `glossary_snapshot`, `whisper_terms_count`, `summary_length` (dans le traitement et le résumé) |

## 15. Impact sur les exports et intégrations

- `metadata.json` : ajout de `summary_length`, `word_budget`, `source_language`, `source_language_forced`, `vocabulary`, `glossary_snapshot`, `template_name`, `num_ctx`.
- `summary.md` : pas de changement de format, seulement une longueur variable.
- Ollama : paramètre `num_ctx` ajouté à tous les appels. Nouveau réglage `LLM_NUM_CTX` dans `.env.example`.

## 16. Sécurité et confidentialité

- Aucune donnée ne quitte la machine. Les notifications sont locales au navigateur.
- Le vocabulaire et les instructions sont des entrées utilisateur injectées dans des prompts : on les encadre avec des balises et on applique des limites de taille (R-7, R-8), mais ils restent sous le contrôle de l'utilisateur local, qui est le seul acteur. Le risque est donc surtout la **transcription**, déjà traitée comme contenu non fiable (F-T.4).
- Stockage local du navigateur (F-11.6) : ne contient que la dernière langue source. Encadré par try/catch ; l'application fonctionne sans.
- Le glossaire global peut contenir des noms de personnes et de clients : il est stocké en base locale, comme les transcriptions, et n'est jamais journalisé en entier (seul le nombre de termes apparaît dans les logs).
- Le texte des notifications se limite au nom du fichier et au statut, sans extrait de contenu.

## 17. Performances

- **Temps de traitement** : un résumé Détaillé de 2 500 mots demande environ 4 000 tokens générés. Sur CPU (~5–10 tokens/s pour un modèle 8B), cela ajoute 7 à 13 min. L'interface indique dans l'infobulle du contrôle que le mode Détaillé est plus long.
- **Mémoire** : passer `num_ctx` à 8192 augmente la mémoire du cache KV. À mesurer sur la configuration CPU minimale ; si nécessaire, le défaut descend à 6144.
- **Recherche** : sur une transcription de 6 h (~350 000 caractères, ~6 000 segments), la mise à jour du surlignage doit prendre moins de 100 ms après le délai de frappe. Au-delà de 1 000 occurrences, seules les 1 000 premières sont surlignées et le compteur affiche « 1 000+ ».
- **Chat** : pas de dégradation attendue ; le contexte de 30 000 caractères tient enfin réellement dans la fenêtre (F-5.6).

## 18. Critères d'acceptation

**Résumé**
- **CA-1** Étant donné une vidéo de 60 min en Standard, quand le traitement se termine, alors le résumé compte entre 450 et 750 mots et contient toutes les rubriques du template, dans l'ordre.
- **CA-2** Étant donné une vidéo de 3 h en Standard, alors le résumé mentionne au moins un élément de chaque tiers de la vidéo (vérifié sur le corpus de test).
- **CA-3** Étant donné un résumé intermédiaire cumulé plus long que 50 % de `num_ctx`, alors l'étape « Consolidation des blocs » apparaît et le traitement se termine sans erreur.
- **CA-4** Étant donné un template dont la rubrique « Actions » contient la consigne « tableau avec colonnes Action | Responsable | Échéance », alors le résumé présente cette rubrique sous forme de tableau.
- **CA-5** Étant donné les instructions supplémentaires « rédige au tutoiement », alors le résumé conserve les rubriques du template **et** est rédigé au tutoiement.
- **CA-6** « Réessayer » sur une vidéo en échec reprend la longueur, la langue source et le vocabulaire d'origine.

**Chat**
- **CA-7** Étant donné une vidéo en français, quand je demande « What were the decisions? », alors la réponse est en anglais.
- **CA-8** Étant donné une question hors sujet posée en anglais, alors la réponse est « This is not mentioned in the video » (ou une formulation équivalente en anglais).
- **CA-9** Étant donné un message « ok ? » après une question en anglais, alors la réponse est en anglais.

**Transcription**
- **CA-10** Étant donné une vidéo en anglais avec une introduction musicale et la langue forcée à `en`, alors `detected_language = en` et l'interface affiche « (forcée) ».
- **CA-11** Étant donné le vocabulaire « Kubernetes, Doñana, OKR » sur une vidéo test qui contient ces termes, alors le taux d'orthographe correcte de ces termes dans la transcription est supérieur à celui obtenu sans vocabulaire (corpus de test).
- **CA-12** Vocabulaire de 1 001 caractères → 422 avec la limite indiquée ; rien n'est enregistré.

**Glossaire global**
- **CA-12a** J'enregistre « OKR, Doñana » dans Paramètres, puis j'importe une vidéo sans vocabulaire, case cochée : les deux termes sont transmis à Whisper et apparaissent dans `metadata.json`.
- **CA-12b** Même import, case décochée : aucun terme du glossaire n'est utilisé et `glossary_snapshot` est vide.
- **CA-12c** Vocabulaire de la vidéo « Okr » et glossaire « OKR » : le vocabulaire effectif contient une seule fois « Okr ».
- **CA-12d** J'importe une vidéo, puis je vide le glossaire, puis je relance la vidéo après un échec : la relance utilise les termes d'origine.
- **CA-12e** Un glossaire de 301 termes → 422 ; le glossaire précédent reste intact.
- **CA-12f** Avec 60 termes longs, seuls les premiers termes jusqu'à 800 caractères sont transmis à Whisper, ceux de la vidéo en premier ; la page de la vidéo indique combien ont été transmis à la transcription et au résumé.

**Templates**
- **CA-13** Je modifie le nom d'un template ; la liste et le menu déroulant de l'import affichent le nouveau nom.
- **CA-14** Je duplique « Cours / formation » deux fois → « Cours / formation (copie) » et « Cours / formation (copie 2) ».
- **CA-15** La suppression du template par défaut est impossible (bouton désactivé, et 409 si l'API est appelée directement).
- **CA-16** Je supprime un template utilisé par une vidéo déjà terminée ; son résumé reste affiché avec la mention « Template supprimé ».
- **CA-17** Après une nouvelle installation, 4 templates sont présents, dont « Compte-rendu de réunion » par défaut. Après en avoir supprimé un et redémarré, il n'est pas recréé.
- **CA-18** Des appels concurrents à « Définir par défaut » sur deux templates ne donnent jamais zéro ni deux templates par défaut.

**Recherche et notification**
- **CA-19** « reunion » trouve « Réunion » et « RÉUNION » ; le compteur est juste ; Entrée passe au suivant et fait défiler jusqu'à l'occurrence.
- **CA-20** Sur une transcription de 6 h, la saisie reste fluide (pas de gel perceptible, < 100 ms).
- **CA-21** Avec la notification autorisée et l'onglet en arrière-plan, la fin du traitement déclenche exactement une notification ; un clic ramène à l'onglet.
- **CA-22** Avec la notification refusée, aucune erreur ; le titre de l'onglet indique la fin.

## 19. Mesure du succès

L'application n'a aucune télémétrie, et ce principe de confidentialité est conservé. La mesure se fait donc sur un **corpus de référence** local et versionné, sans données personnelles :

| Vidéo | Durée | Langue | Intérêt |
|---|---|---|---|
| Réunion | 45 min | fr | Décisions et actions |
| Cours technique | 2 h | fr | Jargon, vocabulaire |
| Podcast | 1 h 30 | en | Chat multilingue |
| Conférence | 3 h | en | Couverture d'une vidéo longue |
| Clip | 3 min | fr | Cas court |

**Protocole de calibrage (décidé) :**
1. **Avant tout développement** : traiter le corpus avec la version actuelle et noter la couverture, la longueur et la durée de traitement (valeurs de référence).
2. **Après livraison du n°5** : traiter le corpus aux trois niveaux de longueur avec les coefficients provisoires de R-1.
3. Ajuster les coefficients jusqu'à atteindre l'objectif de couverture sans résumé délayé sur le clip de 3 min, en respectant la limite de temps de traitement acceptée pour le mode Détaillé.
4. Reporter les coefficients retenus et les mesures dans cette spec (R-1 et ce paragraphe). La phase n'est pas close sans cette étape.

**Indicateurs :**
- **Couverture** : part des sujets de référence (liste établie à la main) présents dans le résumé. Objectif : ≥ 80 % en Standard pour les vidéos de plus de 1 h, contre une valeur actuelle à mesurer avant la phase.
- **Respect du template** : 100 % des rubriques présentes, dans l'ordre.
- **Orthographe du vocabulaire** : taux d'erreur sur les termes fournis, avec et sans vocabulaire.
- **Langue du chat** : 10 questions sur 10 répondues dans la bonne langue.
- **Durée de traitement** : suivie par niveau de longueur, pour mettre à jour l'indication affichée.

## 20. Questions ouvertes et hypothèses

**Décisions (2026-09-22)**
- **Glossaire global** : retenu en phase 1 (F-11.9 à F-11.15, R-9, R-10).
- **Coefficients de R-1** : calibrés sur le corpus de référence selon le protocole du §19 ; étape obligatoire avant clôture.
- **Phase 0** : livrée avant le début de la phase 1 ; elle n'est plus une hypothèse mais un prérequis bloquant.

**Questions ouvertes**
1. **Vocabulaire lié au template** : un template « Cours de Kubernetes » pourrait avoir son propre vocabulaire. C'est plus puissant, mais cela complique le formulaire. À réévaluer après usage du glossaire global.
2. **Plafond de R-1 selon le modèle** : le plafond Standard de 1 500 mots doit-il dépendre du modèle LLM configuré ? Par défaut : non en phase 1.
3. **`hotwords` ou `initial_prompt`** pour le vocabulaire : à décider après mesure (F-11.4).
4. **Liste des langues sources** : se limiter aux 10 proposées, ou exposer les ~100 langues de Whisper avec recherche ?

**Hypothèses**
- Le modèle `qwen3:8b` accepte au moins 8 192 tokens de contexte (sa fenêtre native est plus grande).
- Le défaut de `num_ctx` de la version d'Ollama déployée est **à vérifier** : s'il est déjà suffisant, F-5.6 reste utile pour rendre le comportement explicite et reproductible.
- L'application est ouverte sur `localhost` (contexte sécurisé), une condition nécessaire à l'API Notification.

## Ordre de livraison conseillé

0. **Mesures de référence** sur le corpus avec la version actuelle (§19, étape 1).
1. **F-T + n°12** : templates réellement appliqués et gestion complète. Le n°5 en dépend.
2. **n°5** : longueur du résumé, `num_ctx`, réduction hiérarchique, puis **calibrage de R-1**.
3. **n°11** : langue source, vocabulaire de la vidéo, glossaire global (table, API, section Paramètres, instantané à l'import).
4. **n°7** : langue du chat (utilise aussi le vocabulaire effectif).
5. **n°3** et **n°15** : frontend uniquement, peuvent être développés en parallèle.

## Écarts d'implémentation (2026-09-22)

| Point de la spec | Réalisé | Raison |
|---|---|---|
| §19 étapes 1 à 4 (mesures de référence, calibrage de R-1) | **Non fait le 2026-09-22 ; fait le 2026-09-23** (voir « Calibrage sur le corpus de référence ») | Demande la stack complète avec Ollama et le corpus de 5 vidéos. Les coefficients de R-1 restent provisoires (`backend/app/analysis_options.py` et `frontend/lib/analysis.ts`, à garder synchronisés) |
| F-5.5 : seuil de réduction à 50 % de `num_ctx` | `min(num_ctx / 2, num_ctx − num_predict − 1024)`, au moins 1024 | En mode Détaillé, jusqu'à ~4 000 tokens générés : 50 % d'entrée + la sortie dépasseraient 8 192 |
| F-5.3 : borne de `num_predict` par `LLM_MAX_OUTPUT_TOKENS` | Défaut relevé de 480 à 4 096 (plafond 8 192) | Avec 480, le résumé final aurait été plafonné à ~300 mots quel que soit le niveau. Tous les appels existants fixent leur propre limite, aucun autre comportement ne change |
| F-7.2 : langue de repli « du dernier message non ambigu » | Le texte de cette question est donné au LLM comme référence de langue | Pas de bibliothèque de détection de langue (F-7.5) |
| F-11.8 : « 12 termes (4 de la vidéo, 8 du glossaire) » | « 12 termes de vocabulaire », détail et nombre transmis à Whisper au survol | Les doublons vidéo/glossaire rendaient la somme incohérente |
| §12 : vidéo sans parole « à vérifier » | Résumé fixe « Aucun contenu parlé détecté. », sans appel au LLM | Évite un résumé inventé |
| §14 : `whisper_terms_count` | Plus `llm_terms_count` | Affichage « N pour la transcription » quand ils diffèrent |
| Index `lower(term)` | Vérifié sur PostgreSQL ; SQLite ne sait pas le comparer (avertissement Alembic dans les tests) | Limite de réflexion SQLAlchemy sur SQLite |

**Vérifications faites** : 116 tests unitaires ; 7 tests d'intégration sur PostgreSQL 17 et Redis 7 jetables (migrations, reprise, concurrence du template par défaut) ; parcours navigateur sur l'API réelle (recherche insensible aux accents, glossaire, templates, titre d'onglet pendant et après un traitement).
**Non vérifié** : notification système réelle (navigateur headless), import d'un fichier (ffprobe absent en local), qualité des résumés avec Ollama.

**Anomalie trouvée hors périmètre** : `translate_chunk` limitait la sortie à 480 tokens pour des blocs de 9 000 caractères (~2 500 tokens). **Corrigé le 2026-09-22** : budget proportionnel au bloc (voir `docs/specs/phase-2-naviguer-retravailler.md`).

## Calibrage partiel (2026-09-23)

Le corpus de référence (§19) n'existe pas encore. Mesures faites en lecture seule, dans la stack Docker (`qwen3:8b` sur CPU), sur deux contenus : la vidéo réelle de l'utilisateur (test de PC portables, 11 min, 10 produits ou points de référence) et une réunion de test de 92 s (9 éléments de référence : sujets, décisions, actions).

| Constat | Mesure | Décision |
|---|---|---|
| Tokens par mot | 1,73 (français), 1,40 (anglais), 2,28 (puces avec horodatages), via `eval_count` d'Ollama | `TOKENS_PER_WORD` passe de 1,6 à 2,0, plus 150 tokens fixes (`final_output_tokens`). Avec 1,6, les résumés court et standard étaient coupés avant leurs dernières rubriques (actions perdues). |
| Niveau court | Environ 200 mots écrits quel que soit le budget (5 rubriques obligatoires), soit 154 % d'un budget de 153 | Court = 200 + 2 × D, plancher 200, plafond 350 ; consigne de concision sous 300 mots. Contrôle : 148 mots pour un budget de 203, sans coupure. **Revu sur le corpus** : 200 + 2,5 × D, plafond 450 (voir ci-dessous). |
| Standard et détaillé | Le modèle écrit 66 à 82 % du budget | Coefficients inchangés : le budget sert de maximum souple. |
| Couverture | Perte dans les **résumés par bloc** (6 puces pour environ 8 min de contenu dense) : 4/10 produits en standard | 8 puces par bloc (12 en détaillé) et consigne de couvrir chaque sujet distinct. Standard : 4/10 → 6/10 ; réunion : 7/9 → 9/9. Détaillé : 7/10. |

## Calibrage sur le corpus de référence (2026-09-23)

Corpus du §19 : 5 contenus sous licence libre, de 3 min à 3 h 11 (`data/corpus/README.md`). Chaque contenu est importé par l'API avec le template adapté, puis régénéré en court et en détaillé. La couverture compte les sujets des **descriptions officielles** retrouvés dans le résumé (`references.json`), jamais des sujets tirés de nos transcriptions. Configuration : GPU NVIDIA (RTX 5080), Whisper `large-v3-turbo` en float16, `qwen3:8b` avec `num_ctx` 32 768.

| Contenu | Durée | Court | Standard | Détaillé | Chapitres (début attendu retrouvé) | Traitement complet |
|---|---|---|---|---|---|---|
| Réunion (fr) | 51 min | 5/6 | 5/6 | 6/6 | 21 | 1 min 15 |
| Podcast (en) | 59 min | 5/11 | 6/11 | 7/11 | 23 | 1 min 40 |
| Cours (fr) | 1 h 47 | 5/6 | 5/6 | 6/6 | 31 (2/2) | 3 min 45 |
| Conférence (en) | 3 h 11 | 6/17 | 11/17 | 7/17 | 40 (4/4) | 6 min 25 |
| Clip (fr) | 3 min | 0/4 | 1/4 | 1/4 | 5 | 30 s |

Longueur écrite rapportée au budget : court 53 à 128 %, standard 50 à 126 %, détaillé 39 à 108 %. Le budget reste un maximum souple ; sur les contenus longs, le modèle écrit nettement moins que le budget détaillé.

Décisions prises sur le corpus :

| Constat | Décision |
|---|---|
| Niveau court trop serré au-delà d'une heure : rubriques obligatoires tronquées | Court = 200 + 2,5 × D, plancher 200, **plafond 450** (miroir dans `frontend/lib/analysis.ts`) |
| Résumés par bloc coupés à « [00: » ou sans aucun chapitre quand un seul appel demandait les deux | **Deux appels par bloc** : puces (température 0,1, consigne « synthétise, ne recopie pas »), puis chapitres (température 0) |
| Trop de chapitres sur un contenu long | Limite selon la durée, clamp(3 × √minutes, 3, 40) ; le chapitre le plus proche de son prédécesseur est retiré en premier |
| Résumé final centré sur la fin du contenu | Consigne de couvrir toutes les plages, du début à la fin ; aucune mention de « bloc », « partie » ou « plage horaire » |
| Liste « Chiffres clés » répétée en boucle (court, conférence de 3 h) | Suppression des puces déjà écrites (`drop_repeated_bullets`) |

**Limites connues** :

- La référence du clip est inutilisable : sa description Commons ne décrit pas le contenu, d'où le 0/4.
- La conférence enchaîne quatre exposés dans un seul fichier, et le template « Présentation / démo » suppose un sujet unique. Le détaillé couvre surtout le premier et le dernier exposé. Le nom « PHUZZ » n'est jamais transcrit par Whisper.
- La variance d'un essai à l'autre reste forte : ± 2 sujets sur le podcast et la conférence.

