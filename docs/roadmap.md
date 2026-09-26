# Feuilles de route de Sténo

Les feuilles de route n° 1 et n° 2 n'avaient jamais été écrites dans le dépôt : elles vivaient dans les échanges, et seules les spécifications y renvoient. Ce fichier les regroupe et ouvre la n° 3.

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

**Chapitres** : ils demandent un chapitre toutes les 5 à 10 minutes, entre 2 et 12. Sténo en produit environ 3 × √(minutes) : 21 pour la réunion de 51 min, 40 pour 3 h. C'est à discuter en phase 2, car la mesure ne tranche pas : c'est une question d'usage.

**Ce qu'on ne reprend pas** : les chapitres écrits par un assistant en ligne (Claude Code, Codex). La transcription quitterait l'ordinateur, alors que Sténo fait tout avec Ollama.

### Statut

| Phase | Statut |
|---|---|
| 1 — Mesurer « qui parle » | à faire |
| 2 — Transcription propre | à faire (mesure préalable faite le 2026-09-26) |
| 3 — Nemotron | à faire, décidée par la phase 1 |
| 4 — Deux pistes | à faire |
| 5 — Cartes AMD / Intel | reportée |

### Carte des dépendances

```
Phase 1 — Mesurer « qui parle »  ──┬──> Phase 3 — Nemotron (voix)
   (banc + réunion AMI)           │                 │
                                   │                 v
Phase 2 — Transcription propre <──┘     Phase 4 — Deux pistes à l'enregistrement
   (hallucinations)                        (utilise la phase 3 au sein de chaque côté)

Phase 5 — Cartes AMD / Intel (Vulkan) : indépendante, optionnelle
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
    - pauses de moins de 0,5 s comblées ;
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

### Phase 5 — Cartes graphiques AMD et Intel (optionnelle, reportée)

- **Objectif** : accélérer la transcription au-delà des cartes NVIDIA.
- **Livrable** : une étude seulement — whisper.cpp avec Vulkan comme second moteur de transcription, face à faster-whisper sur le corpus (vitesse, mots perdus ; voir la mesure des lots).
- **Dépendances** : aucune.
- **Pourquoi reportée** : gros chantier (deux moteurs à maintenir), sans utilisateur AMD ou Intel identifié à ce jour.

### Indispensable ou optionnel

| Indispensable | Recommandé | Optionnel ou reporté |
|---|---|---|
| Phase 1 (mesure), Phase 2 (hallucinations), Phase 3 (si la mesure le confirme) | Phase 4 (deux pistes) | Phase 5 (Vulkan) ; scripts d'« actions » personnels ; transcription par lots (problème noté dans `transcription-et-catalogue.md`) |

### Volontairement écarté

- Les chapitres écrits par un assistant en ligne : ils sortiraient la transcription de l'ordinateur.
- L'application de bureau Linux : Sténo reste une application web multiplateforme dans Docker.
