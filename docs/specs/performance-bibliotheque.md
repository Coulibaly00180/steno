# Temps par étape, bibliothèque à grande échelle, découpage du code

| | |
|---|---|
| Statut | Mesuré et livré (2026-09-25) |
| Origine | Axes d'amélioration du projet : suivi continu des durées, pagination, `main.py` et page vidéo monolithiques |
| Migration | `0014_stage_times` |

## Temps par étape

- **Enregistrement** : le worker horodate chaque changement d'étape (`processing_jobs.stage_times`, `app/stage_times.py`).
  - En fin de tâche, les secondes par étape et l'attente dans la file sont gardées avec la durée (`job_durations.stages` et `queued_seconds`).
  - Ces durées restent après la suppression de la vidéo.
  - Une étape quittée puis reprise, comme la transcription interrompue par une attente, compte pour son total.
- **Affichage** : `GET /performance?kind=FULL` alimente la section « Temps de traitement » de la page Modèles.
  - Pour chaque étape : la médiane pour une heure de média, sur les 50 derniers traitements (3 au moins).
  - Sa part du total.
  - L'écart des 10 derniers traitements avec les précédents, signalé au-delà de 15 %.
  - Un ralentissement après un changement de modèle, de prompt ou de pilote, ou un jeu qui occupe la carte, se voit ainsi sans relancer le corpus de référence.

## Bibliothèque à grande échelle

**Mesure** (`backend/scripts/bench_library.py`, pile de test uniquement : le script refuse toute base dont le nom ne finit pas par `_test`) :
- 3 000 vidéos d'une heure, soit environ 125 Mo de transcriptions ;
- 2 tags par vidéo, 3 actions, 5 personnes citées.

| Appel | Avant | Après |
|---|---|---|
| Bibliothèque | 30 ms, **500 vidéos sur 3 000** (les autres hors d'atteinte), 335 Ko | 16 ms, page de 100, total dans `X-Total-Count`, 67 Ko |
| Recherche « budget stagiaire » | 648 ms, 348 Ko | 545 ms, 79 Ko |
| Actions à faire | 74 ms, **2 000 sur 4 500**, 811 Ko | 13 ms, page de 200 avec `total`, 81 Ko |

- **Pagination** :
  - L'ordre complet de la bibliothèque (y compris la fusion avec la recherche par le sens) est calculé sur les seuls identifiants, puis seules les lignes de la page sont chargées. Aucune vidéo n'est plus hors d'atteinte.
  - L'interface charge 100 vidéos, puis 100 de plus à chaque « Afficher plus ». Le total s'affiche en titre.
  - Les actions arrivent par pages de 200. Les exports CSV et `.ics` restent complets.
  - Les responsables sont lus par un `DISTINCT` au lieu d'un second parcours de toutes les actions.
  - La page Questions demande explicitement 500 vidéos, le maximum que lit le chat sur la bibliothèque.
- **Extraits de recherche** : le mot est cherché tel qu'il a été tapé, et le texte n'est débarrassé de ses accents qu'à défaut. Enlever les accents de 30 transcriptions entières coûtait 200 ms ; `COALESCE` s'arrête à la première position trouvée.
- **Limite connue, gardée volontairement** : le classement par pertinence (`ts_rank`) coûte 389 ms quand 3 000 vidéos d'une heure contiennent les mots cherchés, contre 1 ms pour un tri par date (`EXPLAIN ANALYZE`). Il lit le vecteur de chaque vidéo trouvée, environ 0,13 ms chacune.
  - Les données synthétiques sont le pire cas : chaque vidéo contient chaque mot.
  - Une vraie recherche ne trouve qu'une partie de la bibliothèque.
  - La pertinence prime. À reconsidérer au-delà de dizaines de milliers d'heures.

## Découpage du code

- **`app/routes/`** : les domaines les plus récents sortent de `main.py` (de 3 760 à 2 970 lignes), un routeur FastAPI chacun :
  - `actions.py` : sources du résumé, actions et décisions, exports Obsidian et e-mail ;
  - `clips.py` ;
  - `series.py` ;
  - `quality.py` : suivi de qualité et temps par étape ;
  - `access.py` : accès réseau et premier lancement.

  `main.py` les inclut en dernier, car ils réutilisent ses aides (`_video_or_404`, `cancel_job`…). Les routes servies sont identiques, comparées par le schéma OpenAPI avant et après.
- **Page vidéo** : le chat (`VideoChat.tsx`) et les exports (`VideoExports.tsx`) deviennent des composants. Le chat reste monté quand un autre onglet est affiché : une réponse en cours d'écriture continue, comme avant.
- **Tests** : les fixtures donnent la base de test aux modules de `app/routes/` (`MODULES` de `test_phase7`, `db_session` de `test_phase3`).
- **Vérifié sur la vraie pile** : une analyse complète enregistre ses 9 étapes, et le chat répond avec l'horodatage cité. Son historique survit à un changement d'onglet, et les 10 exports sont présents.
