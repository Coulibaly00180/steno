# CLAUDE.md

@AGENTS.md

## Rappels pour Claude Code

- **Tests et projet : toujours dans Docker** (voir « Règle : tout se lance dans Docker » ci-dessus). Pas de venv ni de `npm` sur l'hôte pour valider un changement.
- Vérification visuelle : l'application tourne sur http://127.0.0.1:3000. Les captures Playwright se créent dans `.playwright-mcp/` : supprimer ce dossier à la fin.
- Les agents spécialisés sont dans `.claude/agents/`, les skills produit dans `.claude/skills/`.

## Feuilles de route

- **Référence** : `docs/roadmap.md`. Elle contient l'historique (n° 1 et n° 2, livrées), puis la feuille de route en cours, découpée en phases : objectif, livrables, dépendances, critères de sortie chiffrés, risques, et un statut par phase.
- **Une demande de nouvelle feuille de route** ou de nouveaux « axes d'amélioration » **complète** `docs/roadmap.md` par une nouvelle section numérotée. Ne jamais réécrire une feuille de route livrée.
- **Implémenter un point ou une phase** :
  1. travailler sur une branche dédiée ;
  2. écrire la spécification du lot dans `docs/specs/`, avec une ligne « Origine » qui cite la feuille de route et ses points (« Feuille de route n° 3 : phase 2 ») ;
  3. à la livraison, mettre à jour le statut de la phase dans `docs/roadmap.md` (à faire → en cours → livrée, ou écartée) ;
  4. ajouter les fichiers de référence au tableau « Repères du code » d'`AGENTS.md`.
- **Mesurer avant et après** : un choix de performance ou de qualité s'appuie sur une mesure. Moyens disponibles :
  - le corpus de référence (`data/corpus`, `/quality`) ;
  - les temps par étape (`/performance`) ;
  - `backend/scripts/bench_library.py` ;
  - le banc « voix » une fois livré (feuille de route n° 3, phase 1).

  Une piste écartée après mesure est documentée avec ses chiffres (exemple : la transcription par lots, `docs/specs/transcription-et-catalogue.md`), pour qu'on ne la retente pas à l'aveugle.
- **S'inspirer d'un projet externe** (article, dépôt) :
  - lire le code, l'historique des commits, les tickets et les pull requests, pas seulement le README ;
  - distinguer les chiffres de l'auteur (non reproduits) de ce qui a été vérifié sur Sténo ;
  - citer les sources dans la feuille de route.
- **Rester local** : aucune piste qui ferait sortir des données de l'ordinateur (assistant en ligne, service cloud) n'entre dans une feuille de route.
