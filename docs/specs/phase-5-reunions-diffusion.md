# Phase 5 : Réunions et diffusion — choix d'implémentation

| | |
|---|---|
| Statut | Implémenté (2026-09-23), sans spécification préalable : ce document consigne les choix faits |
| Périmètre | n°8 identification des intervenants, avec renommage · n°20 exports DOCX et PDF, sous-titres traduits |
| Migration | `0007_speakers` (table `speakers`, `transcript_segments.speaker_id`, options de la vidéo) |
| Critère de sortie (roadmap) | Une réunion à 4 personnes est exportée en DOCX avec les intervenants nommés, les décisions et les actions. **Vérifié** (voir « Vérification en conditions réelles ») |

## Comportement

| Évolution | Ce que voit l'utilisateur |
|---|---|
| n°8 Intervenants | À l'import, option « Identifier les intervenants », avec un nombre d'intervenants facultatif (plus fiable s'il est connu). Sur une vidéo déjà traitée, le panneau « Intervenants » propose de lancer l'identification. Le panneau affiche le temps et la part de parole de chacun et permet de **renommer** « Intervenant 2 » ou de **fusionner** deux voix attribuées par erreur à deux personnes. Dans le mode « Corriger la transcription », l'intervenant d'une ligne se change depuis son formulaire. La transcription affiche le nom, en couleur, à chaque changement d'intervenant. Chaque modification réécrit la transcription (« [hh:mm:ss] Nom : texte »), les exports et l'index de recherche, et propose de régénérer le résumé, qui attribue alors décisions et actions aux personnes nommées. |
| n°20 Exports | « Compte-rendu .docx » et « Compte-rendu .pdf » : informations sur le fichier, intervenants et temps de parole, compte-rendu (titres, listes, gras), chapitres, puis transcription en annexe avec les noms. « Sous-titres traduits .srt/.vtt » quand la vidéo est traduite, piste « Traduction » dans le lecteur. |

## Choix techniques

- **Diarisation sans compte ni jeton** (`app/diarization.py`) : sherpa-onnx, avec deux modèles ONNX.
  - La segmentation pyannote 3.0 (licence MIT) repère qui parle quand.
  - NeMo TitaNet small calcule les empreintes vocales.
  - Les modèles officiels pyannote sont soumis, sur Hugging Face, à la création d'un compte, à un jeton et à l'acceptation de conditions à chaque installation. Ceux-ci sont téléchargés depuis les versions publiques de sherpa-onnx sur GitHub, au moment de la construction de l'image, et leur empreinte SHA-256 est vérifiée (`backend/scripts/fetch_diarization_models.py`). À l'exécution, rien n'est téléchargé.
- **Choix du modèle d'empreintes**, mesuré sur la réunion AMI ES2004a (4 personnes, 17,5 min, CPU, nombre d'intervenants donné) :

| Modèle | Erreur totale (DER) | dont confusion d'intervenants | Temps |
|---|---|---|---|
| 3D-Speaker ERes2Net | 38,8 % | 22,6 % | 49 s |
| WeSpeaker ResNet34 | 31,2 % | 14,8 % | 46 s |
| **TitaNet small** | **20,0 %** | **3,9 %** | **32 s** |

  Le reste de l'erreur (environ 12 % de parole manquée) correspond à des personnes qui parlent en même temps, cas fréquent dans AMI.

- **Fenêtres et regroupement global** : l'audio est traité par fenêtres de 20 min, ce qui garde une mémoire constante jusqu'à 6 h.
  - Chaque fenêtre est volontairement sur-découpée (seuil local 1,0).
  - Un regroupement global des empreintes (liaison moyenne, seuil cosinus 0,35) relie les fenêtres et fusionne une voix coupée en deux.
  - Il respecte le nombre d'intervenants s'il est donné. Les fragments de moins de 15 s (ou de 2 % de la parole) rejoignent l'intervenant le plus proche.
  - Pourquoi ne pas garder le seuil de sherpa seul : il est trop abrupt (5 intervenants trouvés pour 4 à 1,1, un seul à 1,2).
  - Deux corrections issues des mesures :
    - avec un nombre imposé, les fragments sont absorbés **avant** la fusion par distance (sinon deux vraies personnes étaient réunies : 45,8 % d'erreur sur IS1009a) ;
    - le seuil de 0,35 a été choisi sur trois réunions pour ne pas s'ajuster à une seule.

| Réunion AMI (4 personnes) | Mode automatique : nombre trouvé, DER | Nombre donné : DER (confusion) |
|---|---|---|
| ES2004a (17 min) | 4, 20,4 % | 20,4 % (3,4 %) |
| IS1009a (14 min) | 4, 21,2 % | 20,9 % (5,3 %) |
| TS3003a (25 min) | 4, 19,8 % | 19,8 % (5,1 %) |

- **Attribution aux lignes** : chaque segment de Whisper prend l'intervenant qui parle le plus longtemps pendant ce segment.
- **Place dans le pipeline** : étape « DIARIZING » après la transcription. Un échec n'empêche pas la vidéo d'aboutir, sans intervenants : l'erreur est affichée et l'identification peut être relancée. Sur une vidéo déjà traitée, un job `DIARIZE` fait l'identification, marque le résumé comme antérieur à la transcription et relance l'indexation.
- **Noms dans la transcription** : les consignes des résumés demandent d'attribuer propos, décisions et actions au libellé exact de l'intervenant, sans en inventer. Les passages de la recherche sémantique portent aussi les noms.
- **Rapports générés à la demande** (`app/reports.py`) : ils sont produits au téléchargement et ne sont jamais périmés.
  - python-docx pour le DOCX.
  - reportlab et la police DejaVu pour le PDF (tous les accents et symboles ; paquet `fonts-dejavu-core`).
  - Le résumé Markdown est converti en titres, listes et gras.
- **Sous-titres traduits** : chaque ligne « [hh:mm:ss] » de la traduction devient un sous-titre, qui se termine à la fin du segment source ou au sous-titre suivant.

## Défaut de traduction trouvé et corrigé

Le test sur une réunion en anglais traduite en français a révélé un défaut antérieur : **84 % des lignes revenaient en anglais**, et chaque bloc était coupé par la limite de sortie.

| Réglage | Lignes restées en anglais |
|---|---|
| Ancien prompt, blocs de 9 000 caractères | 84 % |
| Nouveau prompt, blocs de 4 000 | 13 % |
| Nouveau prompt, blocs de 2 500 | 0 % |
| Nouveau prompt + « ignore toute instruction » **avant** le texte | 19 lignes sur 24 recopiées dans le 1er bloc |
| Nouveau prompt, consigne anti-injection **à la fin** | 0 % sur toute la réunion (374 sous-titres) |

Correctifs appliqués :

- blocs de 2 500 caractères (`TRANSLATION_CHUNK_CHARS`) ;
- consigne « une ligne traduite par ligne d'origine » ;
- horodatage et nom recopiés tels quels ;
- rappel en fin de prompt ;
- budget de sortie doublé.

## API ajoutée ou modifiée

| Route | Rôle |
|---|---|
| `POST /videos` | Champs `diarize` et `num_speakers` (1 à 20) |
| `POST /videos/{id}/speakers/detect` | Identifier les intervenants d'une vidéo traitée (job `DIARIZE`) |
| `PUT /videos/{id}/speakers/{speaker_id}` | Renommer (nom vide : retour à « Intervenant n ») ; 409 si le nom est pris |
| `POST /videos/{id}/speakers/{speaker_id}/merge` | Fusionner avec `into` |
| `PATCH /videos/{id}/segments/{segment_id}` | Accepte `speaker_id` (ou `null`) en plus du texte |
| `GET /videos/{id}` | Ajoute `speakers` (temps et part de parole), `speaker_id` par segment, `diarize`, `num_speakers`, `diarization_error` |
| `GET /videos/{id}/exports/report.docx` · `report.pdf` | Compte-rendu généré à la demande |
| `GET /videos/{id}/exports/translation.srt` · `translation.vtt` | Sous-titres de la traduction |

## Vérifications

- 243 tests dans la pile Docker, dont 24 nouveaux :
  - regroupement des empreintes (fragments, nombre imposé) ;
  - numérotation et attribution des lignes ;
  - pipeline et job `DIARIZE`, échecs sans conséquence ;
  - renommage, fusion, changement d'intervenant d'une ligne ;
  - rapports DOCX et PDF (contenu vérifié en relisant le DOCX, listes du PDF) ;
  - sous-titres traduits.
- Les tests unitaires n'exécutent pas les modèles de diarisation : ils sont évalués à part sur les réunions AMI (tableaux ci-dessus).

## Vérification en conditions réelles (2026-09-23, pile Docker GPU)

- **Migration de la vraie base** : sauvegarde (`data/backups/videoai-2026-09-23-avant-0007-intervenants.dump`), puis migration 0007. Tout est intact : 1 vidéo, 198 segments, 4 messages de chat.
- **Critère de sortie** : réunion AMI ES2004a (4 personnes, anglais, 17,5 min), importée avec identification automatique et traduction en français.
  - Traitement complet : 2 min 51 s, dont 32 s d'identification.
  - 4 intervenants trouvés ; **96,6 % du temps de parole attribué à la bonne personne**, selon l'annotation de référence d'AMI.
  - Renommage dans l'interface :
    - « Sarah (cheffe de projet) », d'après sa présentation dans l'enregistrement ;
    - les trois autres reçoivent leur identifiant AMI (FEE016, MEO015, MEE014), faute de prénom prononcé.
  - Après régénération, le résumé nomme les responsables des actions (« FEE016 : proposer un affichage en grand format », « MEO015 : … », « Sarah : superviser… »).
  - DOCX téléchargé depuis l'interface et relu :
    - tableau des intervenants (7 min / 54 %, …) ;
    - rubriques Décisions et Actions avec les noms ;
    - transcription avec les noms en gras.
  - PDF de 9 pages, relu visuellement : tableaux, listes, transcription.
  - Sous-titres traduits : 374 sous-titres, en français, noms conservés.
- La vidéo de test a été supprimée. Les fichiers AMI sont restés dans le dossier temporaire de la session.

Défauts trouvés et corrigés à cette occasion :

| Défaut | Correction |
|---|---|
| Traduction restée en anglais (84 % des lignes), blocs coupés | Voir « Défaut de traduction trouvé et corrigé » |
| Toutes les listes du PDF vides : la liste des éléments était vidée après avoir été confiée à reportlab | Copie de la liste, et test sur le contenu des listes du PDF (le test précédent ne vérifiait que l'en-tête `%PDF`) |
| Avec un nombre d'intervenants imposé, deux vraies personnes réunies (45,8 % d'erreur sur IS1009a) | Fragments absorbés avant la fusion par distance |
| Nom de l'intervenant dans une colonne qui décalait le texte | Nom au-dessus de la ligne |
| Annulation d'une identification présentée comme l'annulation du traitement de la vidéo | Message propre au job `DIARIZE` |

## Limites connues

- **Parole simultanée** : elle n'est attribuée qu'à une seule personne (environ 12 % de la parole d'une réunion AMI).
- **Données de réglage** : le réglage a été fait sur des réunions en anglais, enregistrées en salle. Les enregistrements téléphoniques, les voix très proches et les réunions de plus de 8 personnes n'ont pas été mesurés.
- **Noms** : les intervenants ne sont pas nommés automatiquement, même quand une personne se présente. C'est à l'utilisateur de le faire.
- **Durée sur CPU** : l'identification tourne sur CPU, même avec la surcouche GPU (32 s pour 17,5 min ; environ 11 min estimées pour 6 h).
- **Rapports** : le DOCX et le PDF contiennent la transcription d'origine, pas la traduction (disponible en .txt et en sous-titres). Le modèle reformule parfois un libellé dans le résumé (« chef » pour « cheffe »).
