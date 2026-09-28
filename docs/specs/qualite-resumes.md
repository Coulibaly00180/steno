# Qualité des résumés : les contenus longs et la mesure

| | |
|---|---|
| Statut | Livré (2026-09-28) |
| Origine | Axes d'amélioration du 2026-09-27, point 4 (« qualité des résumés, mesurée sur le corpus ») |
| Migration | aucune |

## Ce qui change pour l'utilisateur

- **Les vidéos longues sont résumées en entier.** Un résumé de conférence de 3 h 15 couvrait surtout le premier exposé. Les deux derniers finissaient en vrac dans « Prochaines étapes », ou disparaissaient.
- **Les contenus longs** (8 plages horaires ou plus, soit environ 1 h 30 de parole) commencent par une rubrique **« Déroulé »** : une puce par plage, avec son horodatage, son sujet et ses noms propres.
- Les résumés intermédiaires ne sont plus coupés en cours de route.

## Diagnostic : où les sujets se perdaient

Un résumé passe par trois étages :
- des résumés de blocs d'environ 12 000 caractères ;
- des fusions, si le tout dépasse le prompt final ;
- le résumé final.

`backend/scripts/summary_stages.py` cherche les sujets attendus du corpus à chaque étage, sur les transcriptions en cache.

| Fichier | Transcription | Blocs | Résumé final |
|---|---|---|---|
| clip-fr (3 min) | **1/4** | 1/4 | 1/4 |
| conference-en (3 h 15, 17 blocs) | 16/17 | 16/17 | **7/17** |
| podcast-en (1 h, 5 blocs) | 11/11 | 9/11 | **6/11** |
| cours-fr (1 h 50, 10 blocs) | 6/6 | 6/6 | 5/6 |
| reunion-fr (51 min, 3 blocs) | 6/6 | 6/6 | 6/6 |

Trois causes :

1. **La mesure comptait des sujets jamais dits.** Les références viennent des descriptions officielles, qui en disent plus que l'enregistrement. Dans les 3 minutes de clip-fr, « Creative Commons », « réutilisables » et « vidéastes » ne sont jamais prononcés : son résumé ne pouvait pas dépasser 25 %.
2. **Le résumé final perdait la seconde moitié des contenus longs.** Aucun regroupement n'intervient, les 17 blocs tiennent dans le prompt final. Mais la rubrique imposée par le template d'un exposé unique (« Problème », « Solution »…) absorbait le premier exposé, et le reste disparaissait.
3. **Des résumés de blocs coupés** par la limite de sortie : 3 blocs sur 35 du corpus. Un bloc coupé perd sa dernière puce, donc son dernier sujet.

## Changements

| Changement | Où |
|---|---|
| Le score ne compte que les sujets **dits dans l'enregistrement**, les autres sont listés à part (« jamais dit, non compté » dans le panneau Qualité) | `quality.score_item(transcript=…)`, `QualityPanel.tsx` |
| Rubrique **« Déroulé »** en tête du résumé à partir de 8 plages horaires, une puce de 25 mots au plus par plage | `llm.OUTLINE_SECTION`, `final_summary(outline=…)`, `compose_summary` |
| Place de sortie du résumé final × 1,7 quand il y a un Déroulé | `OUTLINE_LENGTH_FACTOR` |
| Limite de sortie des résumés de blocs : 420 → **600** jetons (880 en mode détaillé) | `summarize_chunk` |

## Mesures (2026-09-28, qwen3:8b sur RTX 5080)

Sujets retrouvés dans le résumé final, sur les seuls sujets dits. Le résumé final varie d'un lancement à l'autre : chaque variante a été lancée plusieurs fois.

| Fichier | Avant | Après | Longueur après / budget |
|---|---|---|---|
| conference-en | 7, 9, 11 sur 16 | **15, 15, 15** | 2 422 / 1 500 |
| cours-fr | 5, 5, 6 sur 6 | **6, 6, 6** | ~1 680 / 987 |
| podcast-en | 6, 5, 6, 5 sur 11 (blocs à 420 jetons) | **8, 7, 8, 8** (blocs à 600) | ~510 / 602 |
| reunion-fr | 6/6 | 6, 6, 6 | ~570 / 540 |
| clip-fr | 1/1 | 1/1 | 162 / 250 |

Score du corpus complet, recalculé avec la nouvelle règle : **88,3 %** pour la passe précédente. Aucun fichier ne recule d'une passe à l'autre, hors variation du résumé final.

### Pistes écartées, avec leurs chiffres

- **Déroulé pour tous les contenus** : sur le podcast (5 plages), 5 et 7 sujets sur 11, contre 8 et 8 sans. D'où le seuil de 8 plages.
- **Fusion équilibrée** (regrouper les blocs en ~6 groupes avant le résumé final) : conférence 5 à 6 sur 17, cours 4 à 5 sur 6. C'est pire : la fusion écrase les détails.
- **Premier Déroulé, sans limite de longueur** : conférence 3 251 mots pour 1 500, cours 1 918 pour 987. La limite de 25 mots par puce ramène la conférence à 2 422 mots, à couverture égale.

## Limites

- **Les résumés longs dépassent leur budget** (environ 1,6 fois pour les contenus de plus d'1 h 30), car le Déroulé s'ajoute aux rubriques du template. C'est un choix : il sert de table de navigation, avec les horodatages.
- **Le résumé final varie** d'un lancement à l'autre : ±2 sujets sur le podcast. Une passe de qualité seule ne tranche pas un écart de cette taille.
- Des **noms propres** restent perdus dès les résumés de blocs : Greg Grandin sur le podcast. La transcription écorche aussi des noms (« PHUZZ » entendu « Fuzz ») ; le glossaire est la réponse, pas le résumé.
- Les titres de rubrique restent parfois en français dans un résumé en anglais, malgré la consigne. Non traité ici.

## Outil

```sh
docker compose -f compose.yaml -f compose.gpu.yaml run --rm --no-deps -v "$PWD/backend:/app" worker \
    python -m scripts.summary_stages conference-en podcast-en
```

L'outil demande une passe de qualité au préalable, pour disposer des transcriptions en cache. Il n'écrit rien en base.
