# AGENTS.md — Sténo

Application 100 % locale : import de vidéos ou d'audio (≤ 6 h), transcription (faster-whisper), traduction et résumé (Ollama, `qwen3:8b`), chat sur la vidéo. Backend FastAPI + SQLAlchemy + PostgreSQL + Redis/RQ (`backend/`), frontend Next.js 15 / React 19 (`frontend/`), tout orchestré par Docker Compose.

## Règle : tout se lance dans Docker

**Les tests et le projet se lancent dans Docker, jamais dans un environnement Python ou Node local.** Les images fixent les versions (Python 3.12, Node 22, ffmpeg, PostgreSQL 17) : une exécution locale donne de faux résultats. Par exemple, un npm 11 local a signalé à tort un `package-lock.json` désynchronisé, et Windows convertit les `\n` en `\r\n`.

`make` n'est pas installé sur le poste de développement (Windows) : utiliser directement les commandes `docker compose` ci-dessous. Les cibles du `Makefile` restent valables là où `make` existe.

### Tests (pile isolée `compose.test.yaml`)

```sh
docker compose -f compose.test.yaml run --rm backend-tests    # pyflakes + pytest, avec PostgreSQL et Redis de test
docker compose -f compose.test.yaml run --rm frontend-tests   # npm ci + tsc --noEmit + next build
docker compose -f compose.test.yaml run --rm bench            # banc « voix » : qui parle, mots retrouvés (backend/bench/README.md)
docker compose -f compose.test.yaml down -v                   # nettoyage
```

- La pile de test a son propre nom de projet (`video-ai-test`), sa propre base PostgreSQL en mémoire et son propre Redis. Les tests d'intégration **suppriment le schéma `public`** : ne jamais les pointer vers la base de l'application.
- Le code est monté en volume : aucune reconstruction n'est nécessaire entre deux lancements, sauf si `requirements*.txt` ou `Dockerfile.test` changent (`… build backend-tests`).
- Un changement n'est terminé que si les deux commandes passent.

### Application

**Le poste de développement a un GPU NVIDIA (RTX 5080, 16 Go) accessible depuis Docker : lancer la stack avec la surcouche GPU.** Whisper (CUDA float16) et Ollama tournent alors sur la carte. La commande sans surcouche (CPU) ne sert qu'à reproduire l'environnement d'un utilisateur sans GPU.

```sh
docker compose -f compose.yaml -f compose.gpu.yaml up -d --build # par défaut ici : GPU NVIDIA — http://127.0.0.1:3000 (API : 8000)
docker compose up -d --build            # CPU seul
docker compose -f compose.yaml -f compose.dev.yaml up --build   # rechargement à chaud (CPU)
docker compose --profile tools run --rm model-pull              # télécharge le LLM et le modèle d'embeddings (bge-m3)
docker compose logs -f api worker web
curl -s http://127.0.0.1:8000/status    # état par service (base, Redis, worker, Ollama, modèle)
```

Le service `migrate` applique les migrations Alembic avant le démarrage de l'API et du worker, qui refusent de démarrer sur un schéma en retard.

PostgreSQL tourne sur une image locale (`postgres/Dockerfile`) : `postgres:17-alpine` + pgvector. Ne pas la remplacer par une image Debian (`pgvector/pgvector`) : le volume existant a été créé sous Alpine, et changer de libc corromprait les index de texte.

Un seul worker par défaut : avec une seule carte graphique, deux workers n'apportaient aucun gain mesuré (`docs/specs/performance-workers.md`). `WORKER_REPLICAS=2` reste sûr (transcriptions à tour de rôle, `app/gpu_slot.py` ; tâches orphelines, `app/recovery.py`) ; `docker compose exec worker …` vise alors le premier, `--index 2` le second.

Vérifier que le GPU est bien utilisé : `docker compose exec worker python -c "import ctranslate2; print(ctranslate2.get_cuda_device_count())"` (doit afficher 1) et `docker compose logs ollama | grep "inference compute"` (doit citer la carte NVIDIA).

### Données de l'utilisateur

- `./data` (vidéos, audio, exports) et le volume `postgres_data` contiennent les **vraies données de l'utilisateur**.
- **Sauvegarder avant toute migration ou manipulation de la base** :
  `docker compose exec -T postgres pg_dump -U videoai -Fc videoai > data/backups/videoai-<date>.dump`
- Pour une vérification de bout en bout, créer des médias de test (conteneur jetable avec ffmpeg ou espeak-ng), les importer par l'API, puis **les supprimer** (`DELETE /videos/{id}`). Ne pas modifier les vidéos existantes de l'utilisateur sans son accord.
- Ne jamais lancer `make reset` ni `docker compose down -v` sur la pile de l'application.
- Ne jamais lancer le service `restore` sur la pile de l'application sans l'accord de l'utilisateur : il remplace la base. Les sauvegardes automatiques (`data/backups/steno-*-auto.dump`) et les fichiers de `data/inbox` sont aussi des données de l'utilisateur.

## Migrations

- Schéma géré par Alembic (`backend/migrations/versions/`), guide dans `docs/migrations.md`.
- Nouvelle migration : `docker compose -f compose.yaml -f compose.dev.yaml run --rm migrate alembic revision --autogenerate -m "…"`, puis relire le fichier.
- Conventions : `upgrade()` et `downgrade()` réels ; nouvelles colonnes `nullable` ou avec `server_default` ; contraintes nommées ; aucun import de `app.models` dans une migration.
- `tests/test_migrations.py::test_upgrade_head_matches_models` échoue si un modèle change sans migration.
- Un module qui ouvre sa propre session (`from ..db import SessionLocal`), comme ceux de `app/routes/`, doit figurer dans les fixtures des tests qui remplacent la base (`MODULES` de `tests/test_phase7.py`).

## Repères du code

| Sujet | Fichier |
|---|---|
| API | `backend/app/main.py` ; les domaines récents dans `backend/app/routes/` (actions et exports, extraits, séries, qualité et temps par étape, accès), un routeur chacun, inclus à la fin de `main.py` |
| Pipeline (FULL) et régénération (SUMMARY) | `backend/app/worker.py` (`run_pipeline`, `run_summary`) |
| Prompts LLM | `backend/app/llm.py` |
| Règles métier (longueur, vocabulaire, langues) | `backend/app/analysis_options.py`, en miroir dans `frontend/lib/analysis.ts` : **garder les deux synchronisés** |
| Exports (fichiers) ; comptes-rendus DOCX/PDF générés à la demande | `backend/app/exports.py` ; `backend/app/reports.py` |
| Intervenants : diarisation (Nemotron 3 Diarization par défaut, sherpa-onnx en repli au-delà de 8 voix ; modèles intégrés à l'image par `backend/scripts/fetch_diarization_models.py`, licence dans `backend/models/nemotron/`), libellés dans la transcription | `backend/app/nemotron.py`, `backend/app/diarization.py` (`engine_for`), `backend/app/speakers.py`, `diarize_video` et `run_diarize` (`worker.py`) ; banc `backend/bench/` (`--engine`) |
| État du système | `backend/app/status.py` (`/status` ; `/ready` reste le healthcheck Docker) |
| File d'attente, estimations, annulation | `backend/app/queue_info.py`, `POST /jobs/{id}/cancel` (`main.py`), `JobCancelled` (`worker.py`) |
| Recherche sémantique : passages, embeddings, indexation, questions sur plusieurs vidéos | `backend/app/retrieval.py`, `index_video` et `run_index` (`worker.py`, file secondaire `video-ai-index`), `/library/chat/stream` (`main.py`), page `frontend/app/ask/` |
| Conversations enregistrées (`/library/conversations`), réponses interrompues conservées (`keep()` dans les flux SSE de `main.py`) | `main.py` ; tables `library_conversations` et `library_messages` |
| Bibliothèque (recherche plein texte PostgreSQL, filtres, tags) | `list_videos` et tags dans `main.py` ; colonne `search_vector` créée par la migration 0005 uniquement (voir `app.schema.MIGRATION_ONLY_OBJECTS`) |
| Page vidéo | `frontend/app/videos/[id]/page.tsx` et `frontend/components/` |
| Glossaire qui apprend (corrections → suggestions) | `backend/app/learning.py`, `/glossary/suggestions` (`main.py`) |
| Espace disque, règle des médias à l'import (`source_policy`), job `COMPACT` | `backend/app/storage.py`, `run_compact` et `_apply_source_policy` (`worker.py`) |
| Service `scheduler` : dossier surveillé `data/inbox` et sauvegardes planifiées | `backend/app/scheduler.py`, `watch_folder.py`, `backups.py` ; import partagé avec le formulaire : `import_settings` et `create_import` (`main.py`) |
| Restauration (service outil `restore`), archive portable de la bibliothèque | `backend/app/backups.py`, `backend/app/portable.py`, guide `docs/sauvegardes.md` |
| Mots douteux (probabilité par mot, `transcript_segments.doubts`) | `doubtful_words` (`backend/app/transcription.py`), `frontend/components/TranscriptSearch.tsx` |
| Résumé vérifiable (source de chaque ligne, « à vérifier ») | `backend/app/verification.py`, `/videos/{id}/summaries/{sid}/sources` (`main.py`), `SummaryPanel.tsx` |
| Actions et décisions (extraction après le résumé, dates calculées par le code, CSV / .ics) | `backend/app/actions.py`, `_actions_after_summary` (`worker.py`), routes `/actions` (`main.py`), `ActionsPanel.tsx`, page `frontend/app/actions/` |
| Note Obsidian, archive Obsidian de la bibliothèque, brouillon d'e-mail | `backend/app/notes.py`, `/videos/{id}/note.md`, `/videos/{id}/email.eml`, `/library/export/obsidian.zip` (`main.py`) |
| Suivi de qualité : corpus de référence rejoué (déclenché si prompts ou modèles changent), scores comparés sur les seuls sujets dits ; perte des sujets étage par étage ; « Déroulé » des contenus longs | `backend/app/quality.py`, `worker.compose_summary` (le calcul du résumé sans écriture, partagé avec les analyses), `/quality` (`main.py`), `QualityPanel.tsx`, `backend/scripts/summary_stages.py` ; `OUTLINE_SECTION` (`llm.py`) ; `docs/specs/qualite-resumes.md` |
| Séries de réunions : suggestions par titre, actions ouvertes, « depuis la dernière réunion » | `backend/app/series.py`, routes `/series` et `/videos/{id}/series` (`main.py`), `SeriesPanel.tsx`, pages `frontend/app/series/` |
| Extraits vidéo (tâche `CLIP`, sous-titres incrustés ou en piste) | `backend/app/clips.py`, `run_clip` (`worker.py`), routes `/videos/{id}/clips` et `/clips` (`main.py`), `ClipsPanel.tsx` |
| Accès depuis le réseau : mot de passe, sessions, proxy HTTPS (service `https`, profil `reseau`) | `backend/app/auth.py`, `AccessGuard` (`main.py`), `caddy/Caddyfile`, `AccessPanel.tsx`, page `frontend/app/login/`, guide `docs/acces-reseau.md` |
| Installation Windows (lanceur) et assistant de premier lancement | `windows/steno.ps1`, `Installer Steno.cmd`, page `frontend/app/bienvenue/`, guide `docs/installation-windows.md` |
| Workers en parallèle : une transcription à la fois (verrou Redis), tâches orphelines d'un worker arrêté, estimations de file sur N workers | `backend/app/gpu_slot.py`, `backend/app/recovery.py` (appelé au démarrage des workers et chaque minute par le `scheduler`), `queue_snapshot` (`queue_info.py`) |
| Temps par étape de chaque traitement (`/performance`, page Modèles) | `backend/app/stage_times.py`, colonnes `processing_jobs.stage_times` et `job_durations.stages` |
| Bibliothèque et actions page par page (`X-Total-Count`, `total`) ; mesure à grande échelle | `list_videos` (`main.py`), `/actions` (`routes/actions.py`), `frontend/lib/library.ts` ; `backend/scripts/bench_library.py` (pile de test uniquement) |
| Catalogue des modèles Ollama (lu sur ollama.com toutes les 6 h, variantes et mémoire vidéo estimée, vérification dans le registre avant téléchargement) ; transcription par lots mesurée et écartée | `backend/app/ollama_catalog.py`, `/models/catalog` (`main.py`), `frontend/components/OllamaCatalog.tsx` ; `docs/specs/transcription-et-catalogue.md` |
| Transcription propre : sans reprise du texte précédent, vocabulaire en prompt et en `hotwords`, filtre des boucles et des phrases inventées (`TRANSCRIPTION_VERSION`) | `transcribe_windows`, `is_invented`, `collapse_loops` (`backend/app/transcription.py`), `whisper_hotwords` (`analysis_options.py`) ; `docs/specs/transcription-propre.md` |
| Banc « voix » : réunions françaises générées (Piper) et AMI, erreur de personne, voix trouvées, mots, seuils en intégration continue | `backend/bench/` (`run.py`, `generate.py`, `thresholds.json`), service `bench` de `compose.test.yaml` ; `docs/specs/banc-voix.md` |
| Deux pistes à l'enregistrement (micro à gauche, onglet à droite) : écho retiré par le niveau puis par le texte, chaque côté transcrit à part, « Vous » et « Participants » | `backend/app/sides.py`, `apply_sides` (`speakers.py`), `run_pipeline` et `_diarize_sides` (`worker.py`), `frontend/lib/recorder.ts` ; colonnes `videos.audio_layout`, `transcript_segments.side`, `speakers.side` ; `docs/specs/deux-pistes.md` |
| Chapitres : nombre selon la durée (un toutes les 5 min, jusqu'à 10 pour une vidéo courte, 2 à 30), fusion des plus rapprochés | `chapter_limit`, `keep_main_chapters` (`backend/app/worker.py`) ; `docs/specs/chapitres.md` |
| Réglages modifiés depuis l'interface | `backend/app/app_settings.py` (table `app_settings`) |
| Enregistrement depuis le navigateur (morceaux envoyés au fil de l'eau, `/recordings`) | `/recordings` dans `main.py`, `frontend/app/record/`, `frontend/lib/recorder.ts` |
| Service `live` : transcription en direct d'un enregistrement (aperçu, petit modèle) | `backend/app/live.py` (`LiveDecoder`, `LiveTranscriber`), table `live_segments` |
| Import d'un lien (fichier, flux RSS, plateformes vidéo via yt-dlp si l'option est activée ; étape « Téléchargement » du worker, adresses privées refusées) | `backend/app/url_import.py`, `download_source` (`worker.py`), `frontend/components/LinkImport.tsx`, `UrlImportPanel.tsx` |
| Personnes, organisations, lieux, dates : extraction (tâche de fond `ENTITIES`), fiches, fusion | `backend/app/entities.py`, `llm.extract_entities`, `run_entities` (`worker.py`), `/entities` (`main.py`), pages `frontend/app/entities/` |
| Recherche hybride de la bibliothèque (mots + sens, RRF, seuil `SEMANTIC_MAX_DISTANCE`) et collections | `list_videos` et `/library/searches` (`main.py`) |
| Pouces sur les réponses, export Markdown, recherche et renommage des conversations | `main.py` (colonnes `feedback`), `frontend/components/AnswerFeedback.tsx` |
| Modèles utilisés (LLM, Whisper) choisis dans l'interface, tests de vitesse, VRAM | `backend/app/ai_models.py` (toujours passer par `llm_model()` / `whisper_model()`), `/models` (`main.py`), `gpu_state` et `serve_benchmark` (`live.py`), page `frontend/app/models/` |

Spécifications et choix : `docs/specs/`.

## Conventions

- Interface, messages d'erreur et documentation en français ; code et commentaires en anglais.
- Toute modification de comportement s'accompagne de tests.
- Les vidéos longues (6 h) sont le cas nominal : ne pas charger de fichier entier en mémoire, ne pas re-rendre la transcription complète à chaque mise à jour de lecture.

## Pièges de l'environnement (Windows + Git Bash)

- Dans `bash`, un heredoc ou un `sed` réécrit les `\n` et `\\` d'un code Python ou TypeScript. Pour modifier du code contenant des échappements, passer par l'outil d'édition de fichiers ou un script écrit dans un fichier, jamais par `sed` ou un heredoc.
- Monter un chemin Windows dans un conteneur : `MSYS_NO_PATHCONV=1 docker run -v "C:\\chemin:/out" …`.
