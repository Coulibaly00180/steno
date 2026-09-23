# Claude Product Toolkit

Ce ZIP contient uniquement des subagents et skills Claude Code pour la stratégie produit, les roadmaps, les specs fonctionnelles, les specs techniques, l'UX et la documentation.

## Installation

Copier le dossier `.claude` à la racine de votre projet :

```text
votre-projet/
├── .claude/
│   ├── agents/
│   └── skills/
└── ...
```

Puis redémarrer Claude Code si nécessaire.

## Agents inclus

- `product-strategist`
- `product-manager`
- `functional-analyst`
- `solution-architect`
- `roadmap-planner`
- `ux-product-designer`
- `technical-writer`

## Skills inclus

- `/feature-spec`
- `/technical-spec`
- `/roadmap`
- `/architecture-decision`
- `product-discovery` (principalement déclenché automatiquement selon le contexte)

## Exemples

```text
Utilise product-strategist pour analyser le produit actuel et proposer des opportunités de fonctionnalités basées sur des problèmes utilisateurs.
```

```text
Utilise product-manager puis functional-analyst pour cadrer un MVP de chat avec une vidéo.
```

```text
/feature-spec diarisation multi-speakers avec renommage manuel des intervenants
```

```text
/technical-spec docs/specs/diarization.md
```

```text
/roadmap jusqu'à une V1 production-ready
```

```text
/architecture-decision Choisir entre RQ, Celery et Dramatiq pour les jobs longs
```

## Workflow conseillé

```text
product-strategist
  -> product-manager
  -> functional-analyst
  -> solution-architect
  -> roadmap-planner
  -> développement
  -> technical-writer
```
