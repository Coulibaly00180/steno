# Densité des chapitres

| | |
|---|---|
| Statut | Livré (2026-09-27) |
| Origine | Feuille de route n° 3 : phase 2 (point « densité des chapitres », laissé à discuter) |
| Migration | aucune |

## Décision

**Un chapitre toutes les 5 minutes, jusqu'à 10 pour une vidéo courte, entre 2 et 30** (`chapter_limit`, `backend/app/worker.py`).

| Durée | Avant (≈ 3 × √minutes, 3 à 40) | Maintenant |
|---|---|---|
| 90 s | 4 | 2 |
| 3 min | 5 | 3 |
| 11 min | 10 | 10 |
| 30 min | 16 | 10 |
| 51 min | 21 | 10 |
| 1 h | 23 | 12 |
| 1 h 30 | 28 | 18 |
| 3 h | 40 | 30 |
| 6 h | 40 | 30 |

Le modèle propose toujours ses chapitres librement. Quand il y en a trop, celui qui suit de plus près son prédécesseur est fusionné avec lui, comme avant (`keep_main_chapters`).

## Pourquoi

- **Choix d'usage de l'utilisateur**, sur la vraie réunion de 51 minutes du corpus :
  - avec l'ancienne règle, 20 chapitres, très inégaux : 3 dans les 3 premières minutes, 6 dans les 6 dernières (« Télécharger et compiler le chat », « Fin de la séance et remerciements »), et un seul sur 12 minutes (32:03 à 44:20) ;
  - dans le podcast d'une heure, le Groenland occupait 4 chapitres.
- **Jusqu'à 10 pour une vidéo courte.** Au plus un chapitre par minute : une vidéo courte et dense garde son découpage. C'est le cas documenté en phase 2 (`phase-2-naviguer-retravailler.md`) : 10 ordinateurs portables testés en 11 minutes, un chapitre par produit. Une règle strictement « 1 toutes les 5 min » en aurait donné 2.
- omarchy-meeting-recorder vise 2 à 12 chapitres, un toutes les 5 à 10 minutes. Sténo monte à 30 pour garder un repère toutes les 5 à 12 minutes sur les vidéos de 3 à 6 heures, le cas nominal.

## Mesure (corpus de référence, passe complète du 2026-09-27)

| Fichier | Durée | Chapitres | Débuts de parties attendus retrouvés (à 2 min près) |
|---|---|---|---|
| clip-fr | 3 min | 3 (5 avant) | — |
| reunion-fr | 51 min | 10 (20 avant) | — |
| podcast-en | 59 min | 12 (23 avant) | — |
| cours-fr | 1 h 50 | 21 | 2/2 |
| conference-en | 3 h 15 | 30 | 3/4 |

- Les résumés ne changent pas : couverture des sujets identique sur clip-fr, reunion-fr et podcast-en (25 %, 100 %, 72,7 %). La limite ne s'applique qu'aux chapitres.
- conference-en : le début de la quatrième conférence est attendu à 151:40. Le chapitre le plus proche, « Introduction to Transistors », commence à 154:35, soit 2 min 55 s plus tard, hors de la tolérance de 2 minutes. C'était la première passe complète du corpus : on ne sait pas si l'ancienne règle le retrouvait.

Les chapitres de la réunion de 51 minutes :

```
00:13 Bienvenue à notre réunion d'avril
05:16 Introduction de l'intervenante et objectif de la recherche
08:02 Stratégies pour attirer et soutenir les nouveaux arrivants
11:21 Processus de transition et de rétention
14:40 Activités en dehors du Wiki pour la rétention
21:31 Appréciation et impact des programmes
24:20 Création d'espaces sûrs pour les nouveaux arrivants
32:03 Édition sur les téléphones portables et intégration des nouveaux arrivants
44:20 La page d'accueil des nouveaux arrivants
49:59 Demander un retour sur la séance
```

## Limites

- Un chapitre fusionné garde le titre du premier. « Migrants Dying in Mediterranean and Bird Flu Outbreak » (06:05) couvre ainsi jusqu'à 11:35, en englobant d'anciens chapitres voisins. Les titres ne sont pas réécrits après la fusion.
- Les vidéos déjà analysées gardent leurs chapitres jusqu'à ce que leur résumé soit régénéré.
