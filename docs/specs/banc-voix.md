# Banc « voix » : mesurer qui parle

| | |
|---|---|
| Statut | Livré (2026-09-26) |
| Origine | Feuille de route n° 3 : phase 1 |
| Migration | aucune |

## Ce qui est livré

- **`backend/bench/`** (mode d'emploi dans son `README.md`) :
  - six réunions françaises générées avec des voix de synthèse libres (Piper : siwis, upmc, mls ; attributions dans `NOTICE.md`), avec leur vérité terrain ;
  - la vraie réunion AMI ES2004a (anglais, CC BY 4.0), téléchargée à la demande, avec ses tours de parole de référence de pyannote ;
  - une mesure :
    - part de la parole attribuée à la mauvaise personne ;
    - voix trouvées ;
    - mots retrouvés ;
    - mots attribués à la bonne personne ;
    - lignes inventées dans le silence.
- **Service `bench`** dans `compose.test.yaml`. Il utilise l'image de l'application, qui contient les modèles d'identification des voix, avec le code monté. Les téléchargements sont gardés dans `.bench-cache/`.
- **Intégration continue** : le job « Voices bench » (`.github/workflows/ci.yml`) lance le banc à chaque modification, avec Whisper `small` sur processeur, et échoue sous `backend/bench/thresholds.json`. Les tests unitaires (`tests/test_bench.py`) vérifient qu'un recul fait bien échouer la vérification.

## Mesure de départ (2026-09-26)

Moteur : sherpa-onnx (pyannote segmentation-3.0 + TitaNet), avec le regroupement global de Sténo. Transcription : Whisper `small` sur processeur, comme en intégration continue. L'identification des voix ne dépend pas du modèle Whisper.

| Cas | Erreur de personne | Voix trouvées | Avec le nombre donné | Mots retrouvés | Bonne personne |
|---|---|---|---|---|---|
| `dialogue` (2 personnes) | **0,6 %** | 2/2 | 0,6 % | 97,5 % | 99,2 % |
| `bruit` (2 personnes, micro bruyant) | **2,8 %** | 2/2 | 2,8 % | 94,3 % | 100 % |
| `reunion` (4 personnes) | **40,2 %** | **2/4** | 52,3 % | 88,9 % | 52,9 % |
| `grande-reunion` (6 personnes) | **42,0 %** | **3/6** | 38,4 % | 85,9 % | 54,9 % |
| `musique` (2 personnes) | **40,8 %** | **1/2** | 50,4 % | 82,9 % | 50,6 % |
| `silence` | — | — | — | — | 0 ligne inventée |
| AMI ES2004a, 5 premières minutes | **15,7 %** | **1/3** | 8,8 % | — | 95,1 % |
| AMI ES2004a, 17 minutes | **8,4 %** | 4/4 | 8,4 % | — | 98,4 % |

## Ce que ça dit

- **Deux personnes, ou une vraie réunion longue** : Sténo fait bien, avec 0,6 à 2,8 % d'erreur, et 8,4 % sur les 17 minutes d'AMI (cohérent avec la mesure notée dans `diarization.py`).
- **Réunions courtes à quatre personnes ou plus, et musique : Sténo fusionne des voix.**
  - Il ne trouve que 2 personnes sur 4, 3 sur 6, 1 sur 2 avec musique, et 1 sur 3 sur les 5 premières minutes d'AMI.
  - En cause, la règle qui rattache à une autre voix toute voix de moins de 15 s (ou de moins de 2 % de la parole), et le regroupement des empreintes vocales.
  - **Donner le nombre d'intervenants ne corrige pas** : l'erreur baisse sur AMI 5 minutes, mais augmente sur `reunion` et `musique`.
- **Limite du banc** : plusieurs personnes partagent un même modèle de synthèse (upmc, mls). Leurs voix peuvent se ressembler plus que de vraies voix, et les cas générés sont donc plutôt sévères. AMI sert de contrepoint.
- **La phase 2 tient** : 0 ligne inventée sur 20 s de bruit de pièce.

## Seuils

`thresholds.json` reprenait ces valeurs avec une marge de quelques points : le banc bloque les reculs, pas l'état actuel. Ils ont été relevés avec la phase 3 (Nemotron, `docs/specs/nemotron.md`) : toutes les voix doivent maintenant être trouvées dans chaque cas, et l'erreur de personne reste sous 6 à 8 % sur les réunions générées et sous 14 % sur AMI 5 min.
