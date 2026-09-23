# CLAUDE.md

@AGENTS.md

## Rappels pour Claude Code

- **Tests et projet : toujours dans Docker** (voir « Règle : tout se lance dans Docker » ci-dessus). Pas de venv ni de `npm` sur l'hôte pour valider un changement.
- Vérification visuelle : l'application tourne sur http://127.0.0.1:3000. Les captures Playwright se créent dans `.playwright-mcp/` : supprimer ce dossier à la fin.
- Les agents spécialisés sont dans `.claude/agents/`, les skills produit dans `.claude/skills/`.
