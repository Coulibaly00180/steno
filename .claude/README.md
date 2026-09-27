# Agents et skills Claude Code de Sténo

Les sous-agents sont définis dans `.claude/agents/` et les skills dans `.claude/skills/`. Les règles du projet (Docker, données de l'utilisateur, feuilles de route) sont dans `CLAUDE.md` et `AGENTS.md`, à la racine.

## Agents techniques

| Agent | Domaine |
|---|---|
| `backend-engineer` | FastAPI, SQLAlchemy, PostgreSQL, Redis/RQ, SSE, API |
| `frontend-engineer` | Next.js, React, TypeScript, interface |
| `ai-pipeline-engineer` | ffmpeg, faster-whisper, Ollama, identification des voix, vidéos longues |
| `test-engineer` | Tests de non-régression, API, worker, Compose, bout en bout |
| `security-reviewer` | Revue de sécurité, en lecture seule |
| `docker-devops` | Docker Compose, processeur ou GPU, intégration continue, développement sous Windows |

## Agents produit

| Agent | Rôle |
|---|---|
| `product-strategist` | Problèmes des utilisateurs, opportunités, idées de fonctions |
| `product-manager` | Périmètre d'un lot, récits utilisateur, critères d'acceptation |
| `functional-analyst` | Spécification fonctionnelle : règles, parcours, cas limites |
| `solution-architect` | Spécification technique, architecture, migrations |
| `roadmap-planner` | Feuille de route : phases, dépendances, critères de sortie |
| `ux-product-designer` | Parcours, écrans, états, accessibilité |
| `technical-writer` | Documentation, spécifications, notes de version |

Chemin conseillé pour une fonction nouvelle : `product-strategist` → `product-manager` → `functional-analyst` → `solution-architect` → `roadmap-planner` → développement → `technical-writer`.

## Skills

| Skill | Usage |
|---|---|
| `/feature-spec` | Spécification fonctionnelle d'une fonction |
| `/technical-spec` | Spécification technique à partir d'une spécification fonctionnelle |
| `/roadmap` | Feuille de route (compléter `docs/roadmap.md`, voir `CLAUDE.md`) |
| `/architecture-decision` | Décision d'architecture argumentée |
| `product-discovery` | Découverte produit, déclenché selon le contexte |

## Exemples

```text
Utilise ai-pipeline-engineer pour mesurer whisper.cpp avec Vulkan face à faster-whisper sur le corpus.
```

```text
Demande à backend-engineer et frontend-engineer d'analyser cette fonction en parallèle, puis implémente l'API et l'interface retenues.
```

```text
Utilise security-reviewer sur le diff en cours, sans rien modifier : seulement des constats exploitables.
```

```text
/feature-spec renommer plusieurs intervenants à la fois
```

Après l'ajout d'un dossier `.claude/agents/`, redémarrez Claude Code pour qu'il découvre les fichiers. Pour une fonction large, la session principale orchestre les agents et garde la décision d'intégration.
