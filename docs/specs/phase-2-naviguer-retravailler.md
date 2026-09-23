# Phase 2 : Naviguer et retravailler — choix d'implémentation

| | |
|---|---|
| Statut | Implémenté (2026-09-22), sans spécification préalable : ce document consigne les choix faits |
| Périmètre | n°1 lecteur synchronisé · n°2 timestamps cliquables · n°4 chapitres · n°9 régénérer le résumé · n°10 corriger transcription et résumé |
| Migration | `0004_player_chapters_editing` |

## Comportement

| Évolution | Ce que voit l'utilisateur |
|---|---|
| n°1 Lecteur | Lecteur en haut de la page vidéo. La ligne de transcription en cours est surlignée et suivie (case « Suivre la lecture », désactivée pendant une recherche). Un clic sur l'horodatage d'une ligne lance la lecture à cet endroit. Sous-titres VTT dans le lecteur. Si le navigateur ne sait pas lire la source (MKV, AVI…), la piste WAV extraite pour la transcription est lue à la place. |
| n°2 Timestamps | `[hh:mm:ss]`, `[mm:ss]` et `hh:mm:ss` deviennent des boutons dans le résumé et les réponses du chat. Un `14:30` sans crochets est laissé tel quel (souvent une heure de la journée), de même qu'une position au-delà de la durée. Les prompts demandent désormais de conserver ces horodatages. |
| n°4 Chapitres | Liste à côté du lecteur, chapitre en cours surligné, export `chapters.txt` au format des plateformes vidéo (premier chapitre à 00:00:00). |
| n°9 Régénérer | Bouton « Régénérer » : template, longueur, instructions. Seul le résumé est refait. Les versions précédentes restent consultables (sélecteur). En cas d'échec, la vidéo reste « Terminé » et le résumé précédent est conservé. |
| n°10 Corriger | Transcription : mode « Corriger la transcription » (édition ligne par ligne et « Tout remplacer » avec « Mot entier » / « Respecter la casse »). Résumé : « Corriger » (Markdown). Les exports sont réécrits à l'enregistrement. Après une correction de transcription, un bandeau propose de régénérer le résumé, et la traduction est signalée comme antérieure. |

## Choix techniques

- **Chapitres dans le même appel que le résumé par bloc** : le modèle reçoit le passage une seule fois et répond `CHAPITRES` puis `RÉSUMÉ` (+120 tokens de sortie). Un appel dédié aurait doublé le temps de résumé sur CPU, la lecture du prompt dominant. Les horodatages hors du bloc sont écartés (le modèle peut les inventer), et deux chapitres à moins de 10 s ou de même titre sont fusionnés.
- **Job `SUMMARY` distinct** (`processing_jobs.kind`, fonction `app.worker.run_summary`) : il ne passe pas la vidéo à `PROCESSING`, donc un échec ne touche que le job. Les éditions et régénérations sont refusées (409) pendant un traitement en cours, car le worker écrit les mêmes lignes.
- **Cache des résumés par bloc** (`videos.summary_cache`, JSON) : la clé est l'empreinte du texte résumé, la langue et le niveau (détaillé ou non). Régénérer avec un autre template, les mêmes instructions ou une longueur court/standard ne refait que le résumé final. Une correction de transcription change l'empreinte, donc tout est recalculé.
- **Traduction** : réutilisée par la régénération si elle est postérieure à la dernière correction (`translated_at` ≥ `transcript_edited_at`), refaite sinon.
- **Exports reconstruits depuis la base** (`app/exports.py`), par le worker et par l'API après une correction. Fins de ligne `\n` forcées, identiques sous Windows et Linux.
- **Lecture** : `GET /videos/{id}/media` et `/audio` via `FileResponse` de Starlette, qui gère les requêtes Range (réponse 206), donc le déplacement dans une vidéo de 2 Go sans la télécharger. L'état de lecture est réduit à l'index du segment et du chapitre courants, pour ne pas re-rendre 6 000 lignes quatre fois par seconde.

## API ajoutée

| Méthode | Route | |
|---|---|---|
| GET | `/videos/{id}/media`, `/videos/{id}/audio` | Flux avec Range |
| POST | `/videos/{id}/summaries` | Régénération → `JobOut` (`kind: "SUMMARY"`) |
| PUT | `/videos/{id}/summaries/{summary_id}` | Correction du résumé |
| PATCH | `/videos/{id}/segments/{segment_id}` | Correction d'une ligne |
| POST | `/videos/{id}/transcript/replace` | Rechercher-remplacer littéral (`\1` reste littéral) |

`GET /videos/{id}` expose en plus : `chapters`, `media_kind`, `source_available`, `audio_available`, `summary_outdated`, `translation_outdated`, l'`id` des segments et `edited_at` des résumés.

## Vérifications

- 148 tests dans la pile Docker `compose.test.yaml`, dont les tests d'intégration PostgreSQL et Redis (migration `0004` comparée aux modèles).
- Parcours dans un navigateur sur l'API réelle avec une vraie vidéo MP4 de 90 s : lecture et déplacement par chapitre et par horodatage, suivi de la transcription, « Tout remplacer », correction du résumé avec exports mis à jour, régénération (job en attente puis échec simulé), sélecteur de versions, affichage mobile à 390 px.

## Limites connues

- Chapitres : sur une vidéo de 90 s, le premier sujet (13 s) est absorbé par l'introduction, de façon systématique. Si le modèle ignore le format, la vidéo n'a pas de chapitres. La température 0 n'est pas parfaitement déterministe avec Ollama sur CPU.
- Couverture du résumé sur un contenu très dense : 6/10 produits en standard, 7/10 en détaillé (vidéo de 11 min).
- Longueurs calibrées sur le corpus de référence (phase 1, « Calibrage sur le corpus de référence ») ; le modèle écrit souvent moins que le budget détaillé.
- La traduction n'est pas éditable, et les résumés intermédiaires ne sont pas visibles.

## Vérification en conditions réelles (2026-09-22, stack Docker, `qwen3:8b` sur CPU)

Vidéo de réunion de 92 s en voix de synthèse (espeak-ng), importée par l'API, puis supprimée :

- Pipeline complet (ffprobe, Whisper, traduction, résumé, chapitres, exports) : 2 min 40 s. Régénération : ~90 s. La traduction obsolète a été refaite après correction, et la vidéo est restée « Terminé ».
- Correction et régénération : sur une transcription corrigée (« Tout remplacer » et correction ligne par ligne), le résumé retrouve décisions et actions, que la transcription brute de la voix de synthèse avait perdues.
- Notification : vérifiée dans un vrai Chrome sous Windows, piloté par CDP (Playwright force les pages à rester « visibles »). Fenêtre réduite → onglet `hidden` → à la fin du traitement, notification créée puis événement `show`, émis quand Windows l'affiche.
- Codecs illisibles : MPEG-4 Part 2, MPEG-2 et ProRes se chargent sans erreur, le son joue, mais 0 image est décodée (`videoWidth` = 0) : c'est le cas « son sur écran noir ». La détection bascule bien sur la piste audio extraite (vérifié dans l'application avec un fichier ProRes : lecture et déplacement). HEVC 8 et 10 bits et AV1 sont décodés par Chromium.

Défauts trouvés et corrigés à cette occasion :

| Défaut | Correction |
|---|---|
| Résumé entouré d'un bloc ```` ```markdown ```` | `strip_code_fence` sur le résumé final et les résumés par bloc |
| Résumé en français alors que la cible est l'anglais (une fois sur deux) | Consigne de langue répétée en fin de prompt ; titres de rubriques traduits si le template est dans une autre langue |
| « Le bloc de discussion a confirmé… » : le libellé interne « ### Bloc n » fuyait dans le texte | Résumés intermédiaires étiquetés par leur plage horaire `### [hh:mm:ss] → [hh:mm:ss]` |
| Chapitres supprimés sur les vidéos courtes (fusion sous 30 s) | Écart minimal ramené à 10 s ; consigne « un chapitre par sujet annoncé » |
| Traduction plafonnée à 480 tokens (anomalie de la phase 1) | Budget proportionnel au bloc (`translation_token_budget`) et alerte en cas de coupure |
| Chapitres trop nombreux sur un contenu dense (14 à 19 pour 11 min, un même produit coupé en « présentation » et « analyse ») | Nombre maximal selon la durée (~3 × √minutes : 4 pour 90 s, 10 pour 11 min, 23 pour 1 h, 40 au plus). Les chapitres en trop sont fusionnés avec celui qui les précède de plus près. Température 0 pour l'appel. Résultat : 10 chapitres, un par produit, identiques sur 3 essais. |
| Résumés par bloc trop courts pour un contenu dense | 8 puces par bloc (12 en détaillé) et consigne de couvrir chaque sujet distinct |

## Contenus longs et GPU (2026-09-23, corpus de référence)

Mesuré sur le corpus de la phase 1 (51 min à 3 h 11, voir « Calibrage sur le corpus de référence »).

| Défaut | Correction |
|---|---|
| Fichiers Ogg (`.ogv`, `.ogg`, `.oga`, `.opus`) refusés à l'import | Extensions acceptées côté API et interface |
| `Dockerfile.gpu` ne construisait plus (`nvidia.cublas.lib.__file__` vaut `None`) | Répertoires des bibliothèques NVIDIA trouvés avec `find` |
| Audio de 3 h : worker tué faute de mémoire (faster-whisper décode tout le fichier, 2,5 Go de pic) | Transcription **par fenêtres de 10 min** lues dans le WAV (`app/transcription.py`), coupées au moment le plus silencieux des 15 dernières secondes. La langue de la première fenêtre est imposée aux suivantes. Mémoire du worker : environ 1,1 Go |
| Whisper gardait 2,3 Go de VRAM pendant les résumés : débordement en mémoire partagée, 58 min pour les résumés par bloc d'une conférence de 3 h | Modèle Whisper libéré dès la fin de la transcription (`release_whisper_model`) : 2 min |
| Résumé par bloc tronqué, ou sans chapitres | Deux appels séparés (puces, puis chapitres) |
| Transcription figée une fois (conférence de 3 h, 39 %) jusqu'au délai RQ de 6 h. Rejouée à l'identique : 3 min 30, de 5 à 15 s par fenêtre | Surveillance (`StallWatchdog`) : sans nouveau segment pendant `WHISPER_STALL_TIMEOUT_SECONDS` (30 min par défaut), le job passe en échec (« La transcription s'est bloquée ; relancez le traitement ») et son processus est arrêté |

Configuration GPU (`compose.gpu.yaml`) : Whisper `large-v3-turbo` en float16, `LLM_NUM_CTX` 32 768. Temps du pipeline complet : 1 min 15 pour 51 min de réunion, 6 min 25 pour 3 h 11 de conférence.
