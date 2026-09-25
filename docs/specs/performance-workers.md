# Workers en parallèle : mesure, bug corrigé, réglage par défaut

| | |
|---|---|
| Statut | Mesuré et livré (2026-09-25) |
| Origine | Axes d'amélioration du projet : « un seul worker traite les vidéos une par une » |
| Migration | aucune |

## Hypothèse

Avec un seul worker RQ, les vidéos importées ensemble passent l'une après l'autre. Une micro-mesure sur la RTX 5080 (16 Go) semblait prometteuse :

| 10 min d'audio | Durée | Mémoire vidéo au pic |
|---|---|---|
| Whisper seul | 16,1 s | 3,7 Go |
| LLM seul (résumé) | 12,2 s | 11,3 Go |
| Les deux en même temps | 18,6 s (28,3 s l'un après l'autre) | 13,5 Go |

Deux transcriptions en même temps, à côté du LLM, dépasseraient la carte (environ 18 Go). D'où un verrou : les transcriptions passent à tour de rôle entre les workers.

## Mesure réelle

Le test : trois extraits de 10 minutes (réunion, podcast, cours) importés en même temps par l'API. Le chronomètre court jusqu'à la fin des trois analyses. La carte graphique était libre, et les deux configurations ont été mesurées dans les deux ordres.

| Configuration | 1er passage | 2e passage |
|---|---|---|
| 1 worker | 97 s | 83 s |
| 2 workers | 102 s | 95 s |

**Aucun gain avec une seule carte.**

- Tout le travail lourd passe par la carte : Ollama sert une requête à la fois (`OLLAMA_NUM_PARALLEL=1`), et les transcriptions doivent passer à tour de rôle.
- Un deuxième worker laisse aussi les tâches de fond (index, personnes et dates) prendre le LLM aux analyses en cours. Avec un seul worker, elles attendent que la file principale soit vide.

**Réglage par défaut : 1 worker** (`WORKER_REPLICAS`, dans `compose.yaml` et `compose.gpu.yaml`). Deux workers peuvent servir sur une machine à deux cartes, ou quand beaucoup de travail n'utilise pas la carte : téléchargements de liens, extraits, conversion en audio seul, identification des intervenants.

## Bug trouvé par la mesure (corrigé, utile avec un seul worker aussi)

La première mesure à deux workers a fait échouer une analyse avec l'erreur `prepared statement "_pg3_0" already exists`.

- **Cause** : RQ crée un processus fils (fork) pour chaque tâche. Le fils héritait des connexions PostgreSQL ouvertes par le processus parent (récupération au démarrage, rattrapages), et deux processus parlaient sur la même session PostgreSQL.
- **Correctif** : `app/db.py` appelle `engine.dispose(close=False)` dans le fils après le fork (`os.register_at_fork`). Le fils ouvre ses propres connexions, et celles du parent restent intactes.
- **Test** : `test_a_forked_job_opens_its_own_database_connections` vérifie, sur le PostgreSQL de la pile de test, que le fils obtient une autre session serveur et que celle du parent fonctionne toujours. Plus aucune erreur sur les mesures suivantes.

## Ce qui rend `WORKER_REPLICAS=2` sûr

- **Une transcription à la fois** (`app/gpu_slot.py`) : une clé Redis avec un bail de 2 minutes, renouvelé toutes les 30 s par le processus qui la détient.
  - Si le worker est tué, le bail se libère en 2 minutes au plus.
  - Seul le détenteur renouvelle ou libère le verrou (script Lua) : un bail expiré puis repris par un autre worker ne lui est jamais retiré.
  - Pendant l'attente, l'étape affichée est « En attente de la transcription d'une autre vidéo », et l'annulation reste possible.
  - Les évaluations de qualité (n°4) prennent le même verrou.
- **Tâches orphelines** (`app/recovery.py`) : auparavant, le démarrage d'un worker marquait en échec toutes les tâches en cours, y compris celles de l'autre worker.
  - Désormais, seules sont concernées les tâches qu'aucun worker vivant n'a comme tâche courante dans le registre RQ, avec un délai de grâce de 2 minutes.
  - La vérification a lieu au démarrage et chaque minute dans le service `scheduler`.
  - Si Redis ne répond pas, rien n'est marqué en échec.
- **Rattrapages au démarrage** (index, personnes, évaluation de qualité) : seul le premier worker démarré les lance (clé Redis `steno:worker-startup`, 2 minutes). Rien n'est mis deux fois en file.
- **Estimations de la file** : chaque vidéo en attente va au premier worker libre, au lieu d'additionner toutes les durées.
- **`/status`** affiche « 2 workers · 1 occupé ».
