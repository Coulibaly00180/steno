# Feuilles de route de Sténo

Les feuilles de route n° 1 et n° 2 n'avaient jamais été écrites dans le dépôt : elles vivaient dans les échanges, et seules les spécifications y renvoient. Ce fichier les regroupe et ouvre la n° 3. La n° 4 (2026-10-03) vient de l'étude de Recall.

## Historique

| Feuille de route | Contenu | Où c'est documenté |
|---|---|---|
| n° 1 (20 évolutions) | Socle technique, résultats justes, navigation, tenue de charge, mémoire, réunions et diffusion, finitions | `docs/specs/phase-0` à `phase-6` |
| n° 2 (20 évolutions, toutes livrées) | Autonomie (dossier surveillé, sauvegardes, espace disque), capture (enregistrement, direct, liens), connaissance (personnes, recherche hybride, conversations, modèles), fiabilité (mots douteux, résumé vérifiable, actions, exports), suivi (qualité, séries, extraits, accès réseau, installation Windows) | `docs/specs/phase-7` à `phase-11` |
| Axes de performance | Workers en parallèle (mesurés, un seul par défaut), temps par étape, bibliothèque paginée, découpage du code, catalogue Ollama, transcription par lots (écartée) | `docs/specs/performance-*.md`, `transcription-et-catalogue.md` |

## Feuille de route n° 3 — « Qui parle, et une transcription propre »

### D'où elle vient

Elle vient de l'étude du dépôt [omarchy-meeting-recorder](https://github.com/jankeesvw/omarchy-meeting-recorder) (Linux, Rust, licence MIT, créé le 2026-09-24), présenté par [Korben](https://korben.info/omarchy-meeting-recorder-transcription-locale.html). Son code et son historique ont été lus : `nemotron.rs`, `diarize.rs`, `transcribe.rs`, le banc `bench/`, l'intégration continue.

Ce qu'on en retient :

| Constat dans leur dépôt | Source | Ce que ça dit pour Sténo |
|---|---|---|
| Ils ont remplacé sherpa-onnx (pyannote + regroupement), **la pile actuelle de Sténo**, par Nemotron 3 Diarization de NVIDIA. La part de parole attribuée à la mauvaise personne est passée de **25 % et 54 % à 2 % et 5 %** sur deux réunions réelles, en **dix fois moins de temps**. | Commit `c3f7b41` : leurs mesures, non reproduites ici | L'identification des intervenants est probablement le maillon faible de Sténo |
| Le micro et le son de l'ordinateur sont enregistrés sur **deux pistes**. Le côté de chaque phrase est donc certain, et deux personnes qui parlent en même temps sont gardées toutes les deux. | README, `transcribe.rs` | Le mode « micro + onglet » de Sténo **mixe** les deux sons (`recorder.ts`) et perd cette information |
| L'écho de l'autre côté dans le micro (sans casque) est retiré : le micro n'est gardé que s'il est au moins deux fois moins fort que le son de l'ordinateur, et une phrase répétée au même moment est supprimée. Coût mesuré : **11 %** de temps en plus. | Commits, `own_speech_regions`, `interleave` | À reprendre avec les deux pistes |
| Plusieurs personnes au même micro sont séparées. Le score de bonne personne passe de **74,9 % à 96,6 %** sur leur cas de test. | Commit `b192ee6` | Sténo peut appliquer la même séparation à chaque côté |
| Un banc de test dans l'intégration continue : réunions générées par synthèse vocale libre de droits, réunion réelle AMI, seuils. Toute modification qui fait reculer la reconnaissance des voix est refusée. | `bench/`, `.github/workflows/bench.yml` | Le corpus de Sténo mesure les résumés, pas les voix, et reste sur la machine (médias non redistribuables) |
| Filtre des phrases que Whisper invente sur le silence (« Thank you », « Sous-titres… »). | `is_stock_phrase`, `is_noise_marker` | **Vérifié dans Sténo** : boucles de « Merci. » d'une seconde exactement sur le corpus (6 à la suite sur la fin de `clip-fr`, 2 pile à 600 s sur `reunion-fr`, jonction de deux fenêtres de 10 min) |
| Transcription accélérée par Vulkan : cartes AMD et Intel | whisper.cpp | Sténo n'accélère que les cartes NVIDIA |

**Deuxième lecture : tickets et pull requests du dépôt.**

| Source | Enseignement | Vérifié sur Sténo ? |
|---|---|---|
| PR #1 (Nemotron), mesuré sur deux réunions entières | 78 min à 2 personnes : sherpa-onnx 16,6 % de parole mal attribuée (3 voix trouvées), en 123 s ; Nemotron 2,2 % (2 voix), en 35 s. 57 min à 5 personnes : sherpa-onnx 53,7 % (**2 voix trouvées sur 5**), en 113 s ; Nemotron 5,6 % (5 voix), en 23 s. | Non : c'est l'objet de la phase 1 |
| Ticket #20 (réunion de 26 min en tchèque) | Désactiver la reprise du texte précédent par Whisper a supprimé les boucles et **fait revenir une minute de parole** que la boucle avait avalée. Autres corrections : fusionner les mots ou groupes de mots répétés 3 fois ou plus ; supprimer les crédits de sous-titres dans toutes les langues ; comparer le bruit de la pièce au niveau de bruit propre à la piste. | **Oui**, voir le tableau ci-dessous |
| PR #8, sur un appel de 13 min et un enregistrement de 90 min | Même constat : sans le texte précédent, plus de phrase répétée 7 fois en 6 min 30. | Oui (même mesure) |
| PR #9 | Le vocabulaire passé à Whisper comme prompt (`initial_prompt`) alimente le contexte qui fait boucler. D'où leur choix de corriger les mots mal entendus après coup. | À mesurer : Sténo passe son glossaire par `initial_prompt` |
| Ticket #27 | Leur détection de silence, trop stricte sur un micro bruyant, a gardé 35 trames sur 653 : la transcription n'a couvert que 2 s sur 19. | Sténo utilise Silero VAD, la correction que le ticket recommande ; un cas « micro bruyant » reste à ajouter au banc |

**Mesure sur Sténo (2026-09-26)** : large-v3-turbo sur GPU, avec la reprise du texte précédent (réglage actuel de faster-whisper) puis sans (`condition_on_previous_text=False`).

| Fichier du corpus | Avec reprise (actuel) | Sans reprise |
|---|---|---|
| clip-fr (3 min) | 5 lignes répétées, 6 « Merci. » isolés | **0 répétition, 0 « Merci. »** ; seuls les 7 mots inventés disparaissent |
| reunion-fr (51 min) | 2 répétitions | **0** ; 49 mots en moins et 31 en plus, en des endroits différents |
| podcast-en (59 min) | 0 répétition | 0 ; 43 mots de plus au total, même durée |

À vérifier avant de livrer : le « Merci. » isolé de reunion-fr à 296 s, probablement réel, n'est plus une ligne à part. Et sans reprise, faster-whisper n'applique `initial_prompt` qu'à la première fenêtre de 30 s : le glossaire devra passer par `hotwords`, appliqué à chaque fenêtre.

**Chapitres** : ils demandent un chapitre toutes les 5 à 10 minutes, entre 2 et 12. Sténo en produit environ 3 × √(minutes) : 21 pour la réunion de 51 min, 40 pour 3 h. C'est à discuter en phase 2, car la mesure ne tranche pas : c'est une question d'usage. **Tranché le 2026-09-27** : un chapitre toutes les 5 minutes, jusqu'à 10 pour une vidéo courte, entre 2 et 30 (`docs/specs/chapitres.md`).

**Ce qu'on ne reprend pas** : les chapitres écrits par un assistant en ligne (Claude Code, Codex). La transcription quitterait l'ordinateur, alors que Sténo fait tout avec Ollama.

### Statut

| Phase | Statut |
|---|---|
| 1 — Mesurer « qui parle » | **livrée** le 2026-09-26 (`docs/specs/banc-voix.md`) : bien à 2 personnes et sur AMI 17 min (8,4 %), **fusion de voix** dès 4 personnes en réunion courte (40 à 42 %) et avec musique |
| 2 — Transcription propre | **livrée** le 2026-09-26 (`docs/specs/transcription-propre.md`) ; densité des chapitres tranchée et livrée le 2026-09-27 (`docs/specs/chapitres.md`) |
| 3 — Nemotron | **livrée** le 2026-09-26 (`docs/specs/nemotron.md`) : 4/4 et 6/6 voix trouvées (erreur 1,8 et 4,3 % au lieu de 40 et 42 %), musique 1,3 % au lieu de 40,8 %, AMI 17 min 8,0 % au lieu de 8,4 %, trois fois plus rapide ; seuils du banc relevés |
| 4 — Deux pistes | **livrée** le 2026-09-27 (`docs/specs/deux-pistes.md`) : 100 % des mots du bon côté et 0 ligne d'écho sur `appel` et `appel-haut-parleurs` (10 lignes d'écho sans les filtres) ; reste un essai sur une vraie visioconférence |
| 5 — Cartes AMD / Intel | à faire (décidé le 2026-09-27) : commencer par l'étape 0 sur une machine AMD |

### Carte des dépendances

```
Phase 1 — Mesurer « qui parle »  ──┬──> Phase 3 — Nemotron (voix)
   (banc + réunion AMI)           │                 │
                                   │                 v
Phase 2 — Transcription propre <──┘     Phase 4 — Deux pistes à l'enregistrement
   (hallucinations)                        (utilise la phase 3 au sein de chaque côté)

Phase 5 — Cartes AMD / Intel (Vulkan) : indépendante
```

### Phase 1 — Mesurer « qui parle » (indispensable, en premier)

- **Objectif** : savoir, chiffres à l'appui, à quel point Sténo se trompe de personne, avant de changer quoi que ce soit.
- **Pour l'utilisateur** : rien de visible ; les phases suivantes s'appuieront sur une mesure, pas sur une intuition.
- **Livrables** :
  - un banc « voix » dans la pile de test :
    - la réunion AMI ES2004a (4 personnes, CC BY 4.0, vérité terrain RTTM de pyannote) ;
    - des cas générés en **français** par synthèse vocale libre de droits : paroles qui se chevauchent, deux personnes au même micro, musique de fond, écho, 20 s de silence ;
  - des mesures :
    - part de la parole attribuée à la mauvaise personne ;
    - nombre de voix retrouvées ;
    - mots retrouvés ;
    - lignes inventées dans le silence ;
    - durée ;
  - la mesure de départ de la pile actuelle (sherpa-onnx : pyannote segmentation-3.0 + TitaNet) ;
  - le banc exécuté dans l'intégration continue avec Whisper `small` sur processeur, et des seuils qui empêchent tout recul.
- **Dépendances** : aucune.
- **Critère de sortie** : la mesure de départ est publiée dans une spécification, le banc tourne en intégration continue en moins de 15 minutes, et un recul volontaire fait échouer l'intégration continue.
- **Risques** :
  - trouver des voix de synthèse françaises vraiment libres (Piper : licence de chaque voix à vérifier ; espeak-ng en repli) ;
  - la réunion AMI est en anglais ; le français ne reposera que sur des cas générés.

### Phase 2 — Une transcription propre (indispensable, gain rapide)

- **Objectif** : plus aucune phrase inventée par Whisper sur la musique, le silence ou la jonction des fenêtres, sans perdre de vrais « merci ».
- **Pour l'utilisateur** : fini les « Merci. Merci. Merci. » en fin de vidéo ; des sous-titres et des résumés plus propres.
- **Livrables**, dans l'ordre :
  1. `condition_on_previous_text=False` : la mesure du 2026-09-26 supprime toutes les boucles du corpus ;
  2. le glossaire passé par `hotwords` au lieu de `initial_prompt`, pour qu'il agisse sur toute la vidéo sans relancer les boucles (à mesurer) ;
  3. un filtre étroit pour ce qui resterait :
     - les mots ou groupes de mots répétés 3 fois ou plus, fusionnés ;
     - les crédits de sous-titres supprimés dans toutes les langues (« Sous-titres réalisés par la communauté d'Amara.org », « Sous-titrage ST' 501 ») ;
     - une formule toute faite isolée, retirée seulement quand Whisper doutait qu'il y ait de la parole (`no_speech_prob`) ;
  4. l'examen des jonctions de fenêtres de 600 s ;
  5. des cas « silence », « musique » et « micro bruyant » dans le banc de la phase 1 ;
  6. à discuter : la densité des chapitres (un toutes les 5 à 10 min, contre 21 pour 51 min aujourd'hui).
- **Dépendances** : phase 1, pour les cas silence et musique (le corpus local suffit pour commencer).
- **Critère de sortie** :
  - 0 ligne sur le cas silence ;
  - 0 boucle de « Merci. » sur `clip-fr` et `reunion-fr` ;
  - le « Merci. » isolé de `reunion-fr` à 296 s conservé ;
  - aucun recul à l'évaluation de qualité.
- **Risques** : supprimer une vraie réplique courte. D'où un filtre étroit, et un journal des lignes retirées.

### Phase 3 — Nemotron 3 Diarization pour reconnaître les voix (indispensable si la phase 1 le confirme)

- **Objectif** : moins d'erreurs de personne, et les paroles qui se chevauchent enfin gérées. Aujourd'hui une ligne ne peut avoir qu'un intervenant.
- **Pour l'utilisateur** : les noms d'intervenants justes dans la transcription, les comptes-rendus et les questions.
- **Livrables** :
  - Nemotron 3 Diarization en ONNX int8 (~120 Mo, version épinglée), intégré à l'image comme les modèles actuels ;
  - la préparation audio (log-mel) et la boucle avec mémoire des voix déjà entendues, portées en Python avec onnxruntime et numpy, d'après leur `nemotron.rs` et `Nemotron3DiarizationSpeakerCache` de transformers ;
  - leur post-traitement :
    - seuil de 0,5 ;
    - pauses de moins de 0,5 s comblées (livré : **1,5 s**, mesuré dans `docs/specs/nemotron.md`) ;
    - morceaux de moins de 0,3 s ignorés ;
    - voix de moins de 4 s (ou 4 % de la parole) rattachées à la voix la plus proche ;
    - une phrase entière attribuée à une seule personne ;
  - un réglage pour choisir le moteur, et sherpa-onnx gardé en repli.
- **Dépendances** : phase 1 (la comparaison décide du moteur par défaut).
- **Critère de sortie** : sur le banc, moins d'erreurs de personne que sherpa-onnx sur AMI et sur les cas français, sans ralentir le traitement.
- **Risques** :
  - **8 intervenants au plus**, alors que Sténo en accepte 20 : au-delà, repli sur sherpa-onnx ;
  - licence OpenMDW du modèle à vérifier pour la redistribution dans l'image ;
  - le portage de la préparation audio doit donner les mêmes valeurs que la référence (test de non-régression sur un extrait) ;
  - leurs chiffres portent sur deux réunions : c'est la mesure de la phase 1 qui décide.

### Phase 4 — Deux pistes à l'enregistrement (fortement recommandée)

- **Objectif** : pour les réunions enregistrées depuis Sténo, savoir sans deviner qui est « vous » et qui est « les autres ».
- **Pour l'utilisateur** : « Vous » et « Participant 1, 2… » justes dès l'enregistrement, écho retiré, paroles simultanées gardées.
- **Livrables** :
  - `recorder.ts` en mode « micro + onglet » : le micro sur le canal gauche et le son de l'onglet ou du système sur le canal droit, au lieu du mixage ;
  - côté serveur :
    - les deux canaux séparés, et chaque côté transcrit à part ;
    - l'écho retiré (niveaux, puis trigrammes répétés) ;
    - les voix séparées à l'intérieur de chaque côté (phase 3) ;
    - la fusion par ordre chronologique ;
  - des libellés « Vous », « Vous 2 » et « Participant 1… », renommables comme aujourd'hui.
- **Dépendances** : phase 3 pour séparer plusieurs voix d'un même côté ; sans elle, chaque côté reste une seule personne.
- **Critère de sortie** :
  - sur les cas du banc « appel » et « appel sur haut-parleurs » : au moins 95 % des mots du bon côté ;
  - aucune ligne d'écho ;
  - la transcription en direct et l'envoi des morceaux pendant l'enregistrement inchangés.
- **Risques** :
  - le son d'un onglet ne se capture que dans Chrome et Edge ; celui d'une application de bureau (Teams, Zoom) seulement en partageant l'écran entier avec le son du système, sous Windows. À expliquer dans l'interface ;
  - l'annulation d'écho du navigateur, déjà active, peut interagir avec le filtre : à mesurer.

### Phase 5 — Cartes graphiques AMD et Intel

- **Objectif** : accélérer la transcription au-delà des cartes NVIDIA. Aujourd'hui, sans carte NVIDIA, Whisper et Ollama tournent sur le processeur.
- **Livrable** : d'abord une étude — whisper.cpp avec Vulkan comme second moteur de transcription, face à faster-whisper sur le corpus et le banc « voix » (vitesse, mots perdus, phrases inventées ; voir la mesure des lots). L'intégration seulement si l'étude la justifie.
- **Démarche**, en s'arrêtant dès qu'une étape échoue :
  0. **Faisabilité, sur une machine AMD ou Intel** (une demi-journée) : un conteneur voit-il la carte ? Sous Windows, Docker Desktop (WSL2) passe une carte NVIDIA aux conteneurs ; pour AMD et Intel, c'est à vérifier. Sans accès à la carte depuis Docker, la phase s'arrête là, quel que soit le moteur.
  1. **Qualité de whisper.cpp**, mesurable sur le poste NVIDIA actuel (Vulkan y fonctionne aussi) : corpus de qualité, banc « voix », temps par étape.
  2. **Décision** : qualité proche de faster-whisper et gain réel sur la carte AMD ou Intel → un réglage de moteur, faster-whisper par défaut (comme Nemotron et sherpa-onnx) ; sinon, la piste est documentée avec ses chiffres.
- **Hors périmètre** : l'identification des voix, déjà sur le processeur (Nemotron, 17 min d'audio en 10 s) ; Ollama, qui gère certaines cartes AMD, pose la même question d'accès à la carte que l'étape 0.
- **Dépendances** : aucune.
- **Risques** : deux moteurs de transcription à maintenir, à garder au niveau de faster-whisper (minutages par mot, mots douteux, filtre des phrases inventées, coupure aux silences) ; un processeur AMD ne suffit pas, c'est la carte graphique qui compte (une puce Radeon intégrée au processeur reste une piste, à mesurer).
- **Historique** : reportée jusqu'au 2026-09-27, faute d'utilisateur AMD ou Intel.

### Indispensable ou optionnel

| Indispensable | Recommandé | Optionnel ou reporté |
|---|---|---|
| Phase 1 (mesure), Phase 2 (hallucinations), Phase 3 (si la mesure le confirme) | Phase 4 (deux pistes) ; Phase 5 (Vulkan, si l'étape 0 réussit) | Scripts d'« actions » personnels ; transcription par lots (problème noté dans `transcription-et-catalogue.md`) |

### Volontairement écarté

- Les chapitres écrits par un assistant en ligne : ils sortiraient la transcription de l'ordinateur.
- L'application de bureau Linux : Sténo reste une application web multiplateforme dans Docker.

## Feuille de route n° 4 — « Capturer, relier, retenir » (inspirée de Recall)

### D'où elle vient

Elle vient de l'étude de **Recall** (recall.it, anciennement getrecall.ai), décidée le 2026-10-03. Recall est une base de connaissances qui s'organise toute seule : on y sauvegarde des vidéos YouTube, des podcasts, des articles et des PDF ; il les résume, les relie et les fait réviser. Deux sources :
- le site, la documentation, les tarifs, la feuille de route et les notes de version de Recall (liens en fin de section) ;
- six captures d'écran de l'utilisateur, prises sur une vraie vidéo (« Séminaire Rentrée Triomphale, jour 3 », une vidéo YouTube de plus de 5 h), dans le dossier local `captures/`.

**Ce que fait Recall**, d'après ses pages. Ce sont les chiffres et les affirmations de l'éditeur, non vérifiés ici.

| Domaine | Fonctions |
|---|---|
| Contenus | YouTube (jusqu'à 10 h), podcasts, articles, PDF (300 pages), Google Docs, EPUB, réseaux sociaux (Instagram, TikTok, X, LinkedIn, Reddit, Facebook) avec lecture du texte des images, photos et captures d'écran, Apple News. Import en masse : 1 000 favoris ou liens CSV, 10 000 notes Markdown, Pocket. MP4 envoyés et transfert d'e-mails : seulement annoncés. |
| Une carte par contenu | Onglets **Notebook** (résumé modifiable, éditeur par blocs), **Chat**, **Reader** (texte intégral horodaté), **Quiz**, **Connections**, **Graph** ; vue partagée (« Split ») ; tags automatiques |
| Relier | Mots-clés et entités extraits et enrichis de chaque carte, comptés sur toute la base ; liens manuels `[[` ; fiches d'entités tirées de Wikipédia ; **graphe** (taille d'un nœud selon ses liens, couleur selon les tags, profondeur, filtres par période, source ou sujet) ; **navigation augmentée** : sur n'importe quelle page web, l'extension surligne les mots déjà présents dans la base |
| Retenir | Quiz générés (QCM, vrai/faux, texte à trous, réponse courte, appariement, ordre, cartes) ; révision espacée en 5 étapes, d'un jour à trois mois ; défis partagés par lien, avec classement ; séries et rappels |
| Demander | Chat sur une carte, une sélection (« @ Contexte »), toute la base, le web, ou les deux ; personas ; choix du modèle (GPT, Claude, Gemini, Grok, DeepSeek) en offre Max ; chaque affirmation sourcée et horodatée ; réponse enregistrable dans une carte |
| Écouter | « Listen Mode » : tout contenu lu à voix haute, 30 langues, voix clonable |
| Ailleurs | Extensions Chrome, Firefox, Safari, Edge ; applications iPhone et Android ; vue en tableau ; API et serveur MCP, tous deux en lecture seule |
| Données | « Principalement sur l'appareil », sauvegardées sur Google Cloud en Belgique ; export ZIP Markdown ; pas d'entraînement sur les contenus des utilisateurs. Les résumés, le chat et les quiz passent par leurs serveurs et des modèles en ligne. |
| Prix | Gratuit (10 résumés par mois) ; Plus à 10 $/mois ; Max à 38 $/mois (paiement annuel) |

**Ce que montrent les captures** (vérifié à l'écran) :

| Capture | Constat | Pour Sténo |
|---|---|---|
| Notebook | Résumé en rubriques, chaque puce horodatée ; les entités du texte (Jésus, Dieu, Jéhovah) sont des liens | Sténo a le résumé horodaté et vérifiable ; il n'a pas d'entités cliquables dans le texte |
| Chat | Chaque affirmation porte un horodatage cliquable (▶ 5:03:20) et sa source ; temps de réflexion affiché ; boutons « Ajouter à la carte », pouces, « Sources » ; relance « Il manque le sel ? » bien traitée | Sténo source ses réponses ; il ne peut pas enregistrer une réponse dans une note |
| Reader | Transcription horodatée phrase par phrase, entités en lien, temps de lecture (2 h 44) | Sténo a la transcription horodatée et la recherche ; il n'affiche pas d'entités en lien |
| Connections | Entités par type, avec le nombre de cartes qui les citent ; bouton « Générer plus de connexions ». **Jésus-Christ, Yeshoua, David et YHWH y sont classés « Fictionalcharacter »** | Sténo a quatre types (personnes, organisations, lieux, dates) et des fiches. Un type de trop, et un mauvais classement devient blessant : sur un contenu religieux, « personnage de fiction » est une faute grave |
| Graph | Étoile autour de la vidéo, réglage de profondeur | Sténo n'a pas de graphe |
| Fiche « Jérusalem » | Article Wikipédia importé dans un Reader, avec des **restes de balisage** (« vignette\|upright=1.65 ») et des **chiffres manquants** (« une population de en décembre 2024 ») ; liens Wikipédia, X, Instagram | L'enrichissement par Wikipédia passe par Internet. En local : une copie hors ligne (Kiwix), à extraire proprement |

**Déjà dans Sténo, vu dans les captures** :
- le résumé modifiable ;
- les horodatages cliquables dans les réponses du chat, l'export du chat, les pouces ;
- le chat de bibliothèque restreint à une sélection de vidéos ;
- les fiches de personnes, organisations et lieux, avec fusion.

**Nouveau dans les captures, repris dans les phases ci-dessous** :
- *Notebook* : des notes personnelles par blocs autour du résumé, et une étiquette posée sans action (« Religion »).
- *Connexions et fiche « Jérusalem »* : une fiche d'entité est une page complète (Notebook, Chat, Quiz, Connexions), et un bouton « Générer plus de connexions ».
- *Reader* : la transcription en paragraphes avec horodatages dans le texte, et un temps de lecture (2 h 44).
- *Chat* : « Régénérer », la liste des sources sous la réponse, la portée choisie par « @ », la durée de réflexion affichée (« Thought for 2s »).
- *Notebook* : un sommaire cliquable le long du résumé.
- *Partout* : une liste de premiers pas (« Explore what's possible »), une recherche rapide (Ctrl+F) et un bouton « Écouter » sur le résumé.

**Ce que Sténo fait que Recall ne fait pas** : tout en local ; enregistrement des réunions, deux pistes, intervenants ; fichiers locaux de 6 h ; actions et décisions, séries de réunions, comptes-rendus DOCX et PDF, extraits vidéo.

**Ce que la feuille de route reprend** : les quatre points retenus le 2026-10-03, dans l'ordre du gain pour l'effort. On les construit à la manière de Sténo : local, mesuré, et chaque élément ramené à sa source dans l'enregistrement.

### Statut

| Phase | Statut |
|---|---|
| 1 — « Envoyer à Sténo » depuis le navigateur | à faire |
| 2 — Relier les contenus (vidéos liées, connexions, graphe) | à faire |
| 3 — Réviser (quiz et révision espacée) | à faire |
| 4 — Articles et PDF | à faire, à confirmer après la phase 2 |
| 5 — Lire et demander mieux | à faire |

### Carte des dépendances

```
Phase 1 — Envoyer à Sténo ─────────────> (indépendante ; jetons d'accès utiles aux intégrations)

Phase 2 — Relier ──┬──> Phase 3 — Réviser (questions reliées aux entités et aux passages)
                   └──> Phase 4 — Articles et PDF (un article rejoint le même graphe)

Phase 5 — Lire et demander mieux : indépendante, petits lots livrables un par un
```

### Phase 1 — « Envoyer à Sténo » depuis le navigateur (recommandée, gain rapide)

- **Objectif** : envoyer à Sténo la vidéo ou le podcast ouvert dans le navigateur, en un clic, sans copier de lien.
- **Pour l'utilisateur** : sur une page YouTube, un clic suffit. Choisir le template ou demander les intervenants prend un deuxième clic. Ensuite, une notification « Analyse terminée » mène au compte-rendu.
- **Livrables** :
  - une extension Chrome et Edge (Manifest V3), puis Firefox. Elle envoie l'adresse de l'onglet à l'import par lien (`url_import.py`, qui gère déjà les plateformes vidéo via yt-dlp quand l'option est activée) ;
  - un favori « Envoyer à Sténo » (bookmarklet), pour qui ne veut pas d'extension ;
  - depuis le téléphone, sur le réseau local : Sténo installable comme application web, avec la cible de partage d'Android (« Partager → Sténo ») ;
  - des **jetons d'accès** créés et révoqués dans Paramètres › Accès. Ils permettent à l'extension et aux scripts de passer le mot de passe du réseau (`auth.py`). Ce sont les mêmes jetons que pour les futures intégrations.
- **Dépendances** : aucune.
- **Critère de sortie** :
  - depuis une page YouTube, la tâche est en file en un clic (deux avec les options) ;
  - l'extension refuse d'envoyer quoi que ce soit ailleurs qu'à l'adresse de Sténo configurée ;
  - un jeton révoqué est refusé à la requête suivante.
- **Risques** :
  - la publication sur les magasins d'extensions, ou bien le chargement « non empaqueté » à expliquer ;
  - l'adresse de Sténo : `127.0.0.1` sur le poste, adresse du réseau ailleurs, et le certificat local du proxy HTTPS à faire accepter par le navigateur.

### Phase 2 — Relier les contenus (recommandée)

- **Objectif** : qu'une vidéo ne soit plus une île. On voit d'un coup d'œil ce qu'elle partage avec le reste de la bibliothèque, et on navigue de l'une à l'autre.
- **Pour l'utilisateur** :
  - sur chaque vidéo, une section « Vidéos liées » qui dit pourquoi : mêmes personnes, même sujet, même série ;
  - un onglet « Connexions » : les entités par type, avec le nombre de vidéos qui les citent, chacune cliquable ;
  - les entités cliquables dans le résumé et la transcription ;
  - une vue en graphe de la bibliothèque, avec profondeur et filtres (période, tag, série, type d'entité).
- **Livrables** :
  - un score de parenté entre deux vidéos : entités partagées pondérées par leur rareté, proximité des passages (embeddings `bge-m3` déjà calculés), même série. Calculé en tâche de fond après l'indexation ;
  - routes `/videos/{id}/related` et `/library/graph` (nœuds et liens, limités et paginés pour tenir à grande échelle) ;
  - la vue graphe dans le frontend, avec une bibliothèque de rendu WebGL ou canvas qui tienne des milliers de nœuds ;
  - **Notes de la vidéo** : un bloc de notes personnelles à côté du résumé (Markdown, cases à cocher). « Ajouter à la note » y range une réponse du chat avec ses sources (vu dans les captures). Les notes partent dans l'export Obsidian ;
  - **une fiche d'entité devient une page complète** : les vidéos et passages qui la citent, un chat limité à ces vidéos, puis ses questions de révision (phase 3). C'est la fiche « Jérusalem » de Recall, mais bâtie sur ce qui est dit dans la bibliothèque, pas sur Wikipédia ;
  - **« Chercher plus de connexions »** : à la demande, une extraction plus poussée des entités d'une vidéo (blocs plus petits), pour qui veut plus que le passage automatique ;
  - **des étiquettes suggérées** après l'analyse (« Religion », « Budget »). On les accepte d'un clic, aucune n'est posée sans accord. Ce sont les étiquettes existantes (n° 3), qui restent manuelles aujourd'hui ;
  - En option, à mesurer : enrichir une fiche d'entité depuis une **copie de Wikipédia hors ligne** (Kiwix), téléchargée à la demande. Le texte doit être extrait sans restes de balisage, avec un repli sur un simple résumé, pour ne pas faire ce que montre la fiche « Jérusalem » de Recall.
- **Dépendances** : aucune. Elle s'appuie sur les entités (n° 16), la recherche par le sens (n° 6) et les séries (n° 6 de la feuille de route n° 2).
- **Critère de sortie** :
  - « Vidéos liées » : au moins 4 des 5 premières jugées pertinentes, sur une bibliothèque de test d'au moins 30 vidéos (jugement à la main, consigné dans la spécification) ;
  - le graphe de 1 000 vidéos s'affiche en moins de 2 s (mesuré avec `bench_library.py`) ;
  - aucune entité classée dans un type absent des quatre types de Sténo.
- **Risques** :
  - la qualité des entités : Recall classe Jésus-Christ en « personnage de fiction ». Sténo garde ses quatre types, et une entité douteuse reste sans fiche plutôt que mal étiquetée ;
  - les entités trop fréquentes (« Dieu », « France ») relient tout à tout. Il faut les pondérer par leur rareté et laisser l'utilisateur en masquer ;
  - les performances de la vue graphe sur une grande bibliothèque.

### Phase 3 — Réviser : quiz et révision espacée (optionnelle selon l'usage, utile pour les cours)

- **Objectif** : retenir ce qu'on a regardé, surtout les cours et les formations.
- **Pour l'utilisateur** :
  - un onglet « Quiz » sur chaque vidéo ;
  - une page « Réviser » qui propose chaque jour les questions dues ;
  - chaque question renvoie au passage de la vidéo qui contient la réponse (▶ horodatage). Recall n'a pas ce lien direct au passage.
- **Livrables** :
  - questions générées par le modèle local à partir de la transcription et du résumé : QCM, vrai/faux, réponse courte, cartes. Chaque question porte sa source (horodatage, extrait) ;
  - une vérification de chaque question, comme pour le résumé vérifiable (`verification.py`) : la réponse doit se trouver dans l'extrait cité, sinon la question est écartée ;
  - la révision espacée : étapes et intervalles d'un jour à trois mois, comme Recall, ou l'algorithme SM-2, à trancher sur la simplicité ;
  - l'export des questions vers Anki (fichier `.apkg` ou CSV), pour qui révise déjà avec Anki.
- **Dépendances** : phase 2, pour les questions sur les entités et entre vidéos (facultatif pour un premier lot sur une seule vidéo).
- **Critère de sortie** :
  - sur un échantillon de 50 questions de `cours-fr` et `conference-en`, au moins 90 % ont une réponse juste et trouvable dans l'extrait cité (jugement à la main, consigné) ;
  - aucune question sans source.
- **Risques** :
  - des questions inventées ou ambiguës : la vérification par extrait et un bouton « signaler » y répondent ;
  - un usage incertain en dehors des cours. Le lot commence petit : quiz d'une vidéo, sans page de révision. La suite dépend de l'usage.

### Phase 4 — Articles et PDF (à confirmer)

- **Objectif** : résumer, relier et interroger aussi des textes (articles du web, PDF, puis EPUB), pas seulement de l'audio et de la vidéo.
- **Pour l'utilisateur** : un article ou un PDF s'importe comme une vidéo. On retrouve le même résumé, le même chat, les mêmes entités, dans le même graphe.
- **Livrables** :
  - l'extraction locale du texte. Pour les articles, une bibliothèque d'extraction de lecture, comme trafilatura (licence à vérifier). Pour les PDF, pypdf ou pdfminer.six : pas PyMuPDF, sous licence AGPL. La lecture optique (Tesseract) est en option, pour les PDF scannés ;
  - un type de contenu « document » dans le modèle de données : pas de lecteur, pas d'intervenants. Les horodatages deviennent des numéros de page ou de paragraphe, partout où l'interface en affiche ;
  - les mêmes étapes que pour une vidéo après la transcription : résumé, entités, indexation.
- **Dépendances** : phase 2 (un article rejoint le graphe).
- **Critère de sortie** :
  - sur un corpus d'au moins 10 articles et 5 PDF libres de droits, ajouté à `data/corpus`, couverture des sujets au moins égale à celle des vidéos du corpus ;
  - aucun écran qui suppose un lecteur vidéo ne casse sur un document.
- **Risques** :
  - c'est le plus gros changement de périmètre : Sténo n'était fait que pour l'audio et la vidéo. Il est à confirmer après la phase 2, selon l'usage réel ;
  - les pages web protégées ou chargées par JavaScript : on accepte un échec explicite plutôt qu'un texte vide ;
  - les licences des bibliothèques d'extraction.

### Phase 5 — Lire et demander mieux (recommandée, petits lots)

- **Objectif** : rendre plus agréables la lecture d'une longue vidéo et les questions qu'on lui pose. Ce sont les détails vus dans les captures que Sténo n'a pas.
- **Pour l'utilisateur et livrables**, chacun livrable seul :
  - **vue « Lecture » de la transcription** : les lignes regroupées en paragraphes (par intervenant et par pause), avec les horodatages dans le texte et le temps de lecture. On bascule entre lignes et paragraphes. Sans re-rendre toute la transcription d'une vidéo de 6 h (règle d'`AGENTS.md`) ;
  - **sommaire du résumé** : un plan cliquable des rubriques, utile depuis le « Déroulé » des contenus longs ;
  - **chat** :
    - « Régénérer » une réponse ;
    - sous chaque réponse, la liste des passages cités, sans doublon ;
    - dans le chat de bibliothèque, une portée choisie par « @ » : des vidéos, une étiquette, une série ou une personne (aujourd'hui, seulement une sélection de vidéos) ;
  - **mode « réfléchir »** du chat : la réflexion de qwen3, aujourd'hui coupée (`think: False`), proposée en option, avec sa durée affichée. À mesurer avant de le livrer : justesse sur les questions du corpus, et délai ;
  - **recherche rapide** (Ctrl+K) : vidéos, personnes et passages, depuis n'importe quelle page ;
  - **premiers pas** : une courte liste à cocher (importer, demander, renommer un intervenant, réviser) qui remplace l'assistant affiché une seule fois (`/bienvenue`), et disparaît quand elle est faite.
- **Dépendances** : aucune. La portée par personne et par série du chat profite de la phase 2.
- **Critère de sortie** :
  - vue Lecture d'une transcription de 6 h : premier affichage en moins de 1 s, défilement sans saccade (mesuré) ;
  - mode « réfléchir » livré seulement s'il améliore la justesse sans doubler le délai.
- **Risques** : la dispersion. Six petits lots, chacun avec son test, livrés un par un, pas un grand chantier d'interface.

### Vu chez Recall, à discuter plus tard

- **Écouter** un résumé ou une transcription (« Listen Mode », bouton « Listen » sur le résumé dans les captures) : faisable en local avec Piper, déjà utilisé par le banc « voix ». Les voix de Piper ont chacune leur licence.
- **Partager** une vidéo (bouton « Share » des captures) : en lecture seule, et seulement sur le réseau local, derrière l'accès protégé existant. Jamais par un lien public.
- **Navigation augmentée** : surligner, sur n'importe quelle page web, ce qui est déjà dans la bibliothèque. Ce serait le prolongement naturel de l'extension (phase 1) et du graphe (phase 2). Mais une extension qui lit toutes les pages visitées demande une prudence particulière : comparaison faite dans l'extension, rien envoyé à Sténo.
- **Vue en tableau** de la bibliothèque, **import en masse** (favoris, CSV de liens), **défis partagés** (quiz par lien) : petits lots, selon l'usage.
- **Intégrations** listées le 2026-09-28, à arbitrer :
  - plusieurs dossiers surveillés avec leurs réglages (OBS, Zoom) ;
  - synchronisation automatique d'un coffre Obsidian ;
  - webhook « analyse terminée » ;
  - agenda local ;
  - serveur MCP. Recall en a un, en lecture seule. Dans Sténo, il ne resterait local qu'avec un assistant local.

### Volontairement écarté

- **Le chat par des modèles en ligne** (GPT, Claude, Gemini, comme Recall) et l'enrichissement par Internet : ils sortiraient les contenus de l'ordinateur.
- **Les contenus des réseaux sociaux** : extraction fragile, contournement des conditions d'utilisation, peu utile au cas d'usage de Sténo.

### Sources

- [Recall, accueil](https://www.recall.it/) ; [tarifs](https://www.recall.it/pricing) ; [FAQ](https://www.recall.it/faq) ; [annonce de Recall 2.0](https://www.recall.it/post/recall-2-0-announcement)
- [Documentation](https://docs.recall.it/) ; [graphe](https://docs.recall.it/deep-dives/graph/overview) ; [quiz et révision espacée](https://docs.recall.it/deep-dives/quiz-and-spaced-repetition) ; [chat](https://docs.recall.it/deep-dives/chat/overview) ; [navigation augmentée](https://docs.recall.it/deep-dives/recall-augmented-browsing) ; [API](https://docs.recall.it/developer/api) ; [MCP](https://docs.recall.it/developer/mcp) ; [feuille de route de Recall](https://docs.recall.it/recall-roadmap)
- [Notes de version](https://feedback.recall.it/changelog)
- Captures d'écran de l'utilisateur, 2026-10-03 (dossier local `captures/`, non versionné)
