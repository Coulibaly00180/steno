# Sténo

**Transcrire, résumer et interroger ses vidéos et ses réunions, sans que rien ne quitte l'ordinateur.**

Sténo importe une vidéo ou un fichier audio de 6 heures au plus, ou enregistre une réunion depuis le navigateur. Il en tire :
- une transcription horodatée, avec les intervenants ;
- une traduction ;
- un résumé dont chaque ligne renvoie à sa source ;
- des chapitres ;
- la liste des actions et des décisions.

Il permet aussi de poser des questions sur une vidéo ou sur toute la bibliothèque. Tout tourne en local, dans Docker : transcription avec faster-whisper, langage avec Ollama (`qwen3:8b` par défaut), recherche par le sens avec `bge-m3`.

## Ce que fait Sténo

| Domaine | Fonctions |
|---|---|
| **Entrées** | Import de fichiers, de liens (fichier, flux RSS, plateformes vidéo via yt-dlp si l'option est activée), dossier surveillé `data/inbox`, enregistrement depuis le navigateur avec transcription en direct |
| **Transcription** | faster-whisper, fenêtre par fenêtre (mémoire constante jusqu'à 6 h), vocabulaire et glossaire qui apprend des corrections, mots douteux signalés, filtre des boucles et des phrases inventées sur le silence |
| **Qui parle** | Identification des intervenants avec **Nemotron 3 Diarization** (NVIDIA, sur le processeur), sherpa-onnx en repli. En mode « micro + onglet », ou avec des pistes séparées à l'import (OBS, enregistreur d'appels), **deux côtés** : « Vous » et « Participants » sans deviner, écho des haut-parleurs retiré, jusque dans la transcription en direct |
| **Analyse** | Résumés par template, longueur réglable, **résumé vérifiable** (source de chaque ligne), chapitres, actions et décisions avec dates, personnes, organisations et lieux, séries de réunions (« depuis la dernière fois ») |
| **Questions** | Chat sur une vidéo, questions sur toute la bibliothèque (recherche hybride mots + sens), conversations enregistrées |
| **Sorties** | Sous-titres, comptes-rendus DOCX et PDF, note et archive Obsidian, brouillon d'e-mail, actions en CSV et `.ics`, extraits vidéo sous-titrés |
| **Fonctionnement** | File d'attente avec estimations et annulation, temps par étape, sauvegardes planifiées et restauration, archive portable de la bibliothèque, accès protégé depuis le réseau local, suivi de qualité sur un corpus de référence |

## Démarrer

### Windows, sans ligne de commande

Double-cliquez sur **`Installer Steno.cmd`**. Le lanceur installe ou vérifie Docker Desktop, détecte la carte graphique, construit les services, télécharge les modèles, puis ouvre l'assistant de premier lancement. Guide : [docs/installation-windows.md](docs/installation-windows.md).

### Avec Docker Compose

Il faut Docker, 16 Go de mémoire et 40 Go libres. Une carte NVIDIA est recommandée.

```sh
cp .env.example .env
docker compose -f compose.yaml -f compose.gpu.yaml up -d --build   # carte NVIDIA
docker compose up -d --build                                       # ou : processeur seul
docker compose --profile tools run --rm model-pull                 # modèle de langage et d'embeddings
```

Ensuite :

- l'interface est sur http://127.0.0.1:3000, l'API sur http://127.0.0.1:8000 ;
- `curl -s http://127.0.0.1:8000/status` donne l'état de chaque service ;
- le modèle de transcription se télécharge à la première analyse.

| | Processeur seul | Carte NVIDIA (`compose.gpu.yaml`) |
|---|---|---|
| Transcription | Whisper `small`, int8 | Whisper `large-v3-turbo`, float16 |
| Langage | `qwen3:8b` (`.env`) ; le lanceur Windows choisit `qwen3:4b` sans carte ou sous 10 Go de mémoire vidéo | `qwen3:8b`, contexte de 32 000 jetons |
| Intervenants | Nemotron sur le processeur dans les deux cas (17 min d'audio en 10 s environ) | |

Les modèles se changent depuis la page **Modèles** : catalogue d'Ollama, tests de vitesse, mémoire vidéo.

## Vos données

- Tout est dans `./data` : médias, transcriptions exportées, sauvegardes, dossier surveillé. La base PostgreSQL est dans le volume Docker `postgres_data`.
- Aucun service extérieur n'est appelé pendant l'analyse. Seuls des téléchargements ont lieu : les modèles à l'installation, et les liens que vous importez.
- Les ports 3000 et 8000 n'écoutent que sur l'ordinateur. Pour ouvrir Sténo au réseau local, il faut un mot de passe et le proxy HTTPS : [docs/acces-reseau.md](docs/acces-reseau.md).
- Sauvegardes et restauration : [docs/sauvegardes.md](docs/sauvegardes.md).

## Architecture

```
navigateur ──> web (Next.js 15, React 19) ──> api (FastAPI) ──> PostgreSQL 17 + pgvector
                                                  │
                                                  └──> Redis (RQ) ──> worker : ffmpeg, faster-whisper,
                                                                        Nemotron, Ollama, exports
scheduler : dossier surveillé, sauvegardes, reprise des tâches orphelines
live      : transcription en direct des enregistrements
migrate   : migrations Alembic avant le démarrage de l'API et du worker
https     : proxy Caddy pour le réseau local (profil « reseau », facultatif)
```

| Dossier | Contenu |
|---|---|
| `backend/` | API, worker, pipeline, tests (`backend/tests`), banc « voix » (`backend/bench`) |
| `frontend/` | Interface Next.js |
| `docs/` | Guides, feuille de route, spécifications (`docs/specs/`) |
| `windows/` | Lanceur Windows |
| `caddy/`, `postgres/` | Proxy HTTPS, image PostgreSQL avec pgvector |

## Développer

Les tests et l'application se lancent **dans Docker**, jamais dans un Python ou un Node local : les images fixent les versions (Python 3.12, Node 22, ffmpeg, PostgreSQL 17).

```sh
docker compose -f compose.test.yaml run --rm backend-tests    # pyflakes + pytest (PostgreSQL et Redis de test)
docker compose -f compose.test.yaml run --rm frontend-tests   # tsc + next build
docker compose -f compose.test.yaml run --rm bench            # banc « voix » : qui parle, mots retrouvés, écho
docker compose -f compose.test.yaml down -v                   # nettoyage de la pile de test
docker compose -f compose.yaml -f compose.dev.yaml up --build # rechargement à chaud
```

- Le `Makefile` donne les mêmes commandes là où `make` est installé.
- L'intégration continue (`.github/workflows/ci.yml`) lance les tests, le banc « voix » et la validation des fichiers Compose.
- Repères du code, conventions et pièges de l'environnement : [AGENTS.md](AGENTS.md).

| Document | Sujet |
|---|---|
| [docs/roadmap.md](docs/roadmap.md) | Feuilles de route : historique et phases en cours |
| [docs/specs/](docs/specs/) | Une spécification par lot livré, avec ses mesures et les pistes écartées |
| [docs/migrations.md](docs/migrations.md) | Schéma de la base et migrations Alembic |
| [backend/bench/README.md](backend/bench/README.md) | Banc « voix » |

## Licences

- Code de Sténo : MIT ([LICENSE](LICENSE)).
- Modèles, téléchargés ou intégrés à l'image, chacun sous sa licence :
  - Whisper (MIT), via faster-whisper ;
  - Nemotron 3 Diarization (OpenMDW 1.1, [backend/models/nemotron/](backend/models/nemotron/)) ;
  - pyannote segmentation-3.0 (MIT) et TitaNet, dans leurs exports sherpa-onnx (licences dans leurs dépôts) ;
  - les modèles Ollama choisis (Qwen3 : Apache 2.0 ; bge-m3 : MIT).
- Voix de synthèse et réunion AMI du banc : [backend/bench/NOTICE.md](backend/bench/NOTICE.md).
