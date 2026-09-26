# Nemotron 3 Diarization : reconnaître les voix

| | |
|---|---|
| Statut | Livré (2026-09-26) |
| Origine | Feuille de route n° 3 : phase 3 |
| Migration | aucune |

## Ce qui change pour l'utilisateur

Les intervenants sont reconnus par **Nemotron 3 Diarization** de NVIDIA à la place de sherpa-onnx.

- Dans les réunions courtes à quatre personnes ou plus, et avec une musique de fond, Sténo ne fusionne plus les voix. Avant, il trouvait 2 personnes sur 4, et 1 sur 2 avec musique.
- L'étape est trois fois plus rapide.
- Rien à régler : le modèle est intégré à l'image, et il tourne sur le processeur, sans connexion.

## Mesure (banc « voix », 2026-09-26)

Même banc, même transcription (Whisper `small` sur processeur) ; seul le moteur change.

| Cas | Erreur de personne, sherpa-onnx | Erreur de personne, Nemotron | Voix trouvées (sherpa → Nemotron) | Bonne personne (sherpa → Nemotron) |
|---|---|---|---|---|
| `dialogue` (2 personnes) | 0,6 % | **0,5 %** | 2/2 → 2/2 | 99,2 % → 99,2 % |
| `bruit` (micro bruyant) | 2,8 % | 3,7 % | 2/2 → 2/2 | 100 % → 100 % |
| `reunion` (4 personnes) | 40,2 % | **1,8 %** | 2/4 → **4/4** | 52,9 % → **100 %** |
| `grande-reunion` (6 personnes) | 42,0 % | **4,3 %** | 3/6 → **6/6** | 54,9 % → **96,6 %** |
| `musique` | 40,8 % | **1,3 %** | 1/2 → **2/2** | 50,6 % → **100 %** |
| `silence` | 0 ligne | 0 ligne | — | — |
| AMI ES2004a, 5 min | 15,7 % | **10,6 %** | 1/3 → **3/3** | 95,1 % → **100 %** |
| AMI ES2004a, 17 min | 8,4 % | **8,0 %** | 4/4 → 4/4 | 98,4 % → 98,5 % |

Durée de l'identification, sur processeur : AMI 17 min en **10 s** contre 33 s ; les cas du banc en 0,5 à 3 s contre 1,8 à 7,7 s.

Donner le nombre d'intervenants ne change rien sur le banc : Nemotron les trouve déjà tous.

Le seul recul est le cas `bruit`, de 2,8 à 3,7 %. Il s'agit de parole sans personne attribuée, et aucun mot ne change de personne.

## Comment ça marche

- **Le réseau** : l'export ONNX int8 d'onnx-community (120 Mo, révision `353b6f8`), téléchargé à la construction de l'image avec vérification SHA-256 (`scripts/fetch_diarization_models.py`), dans `/opt/models/diarization/nemotron/`.
  - Licence OpenMDW 1.1 : l'usage commercial et la redistribution sont permis. La licence et les mentions d'origine sont copiées à côté du modèle (`backend/models/nemotron/`).
- **Le traitement, porté en Python** (`backend/app/nemotron.py`, onnxruntime + numpy), d'après Hugging Face transformers :
  - spectrogramme log-mel (128 bandes, préaccentuation 0,97, fenêtre de Hann de 400 échantillons dans 512) ;
  - blocs de 27,2 s, avec 3,2 s d'anticipation ;
  - **mémoire des voix déjà entendues** : 264 trames choisies par score, plus une file des 40 dernières. C'est elle qui garde les numéros d'intervenants d'un bout à l'autre du fichier.
- **Des probabilités aux tours de parole** :
  - une personne parle quand sa probabilité dépasse 0,5 ;
  - ses pauses de moins de **1,5 s** sont comblées, et les morceaux de moins de 0,3 s ignorés ;
  - une voix entendue moins de 4 s, ou pour moins de 4 % de la parole, est rattachée à la voix du tour le plus proche ;
  - avec le nombre d'intervenants donné, les plus bavards sont gardés et les autres rattachés.
- **Mémoire constante jusqu'à 6 h** : l'audio est lu bloc par bloc dans le WAV, et seul un bit par trame et par personne est gardé, soit 17 Mo pour 6 h.

### Pourquoi 1,5 s de pause, et non 0,5 s comme omarchy

Nemotron marque la voix elle-même, jusqu'à la respiration entre deux phrases. Les tours de référence d'AMI, construits à partir des mots, comptent ces respirations dans le tour, tout comme les répliques des cas générés. Et un lecteur fait de même.

| Pause comblée | AMI 5 min | AMI 17 min | `reunion` | `grande-reunion` | `musique` |
|---|---|---|---|---|---|
| 0,5 s | 24,5 % | 18,2 % | 6,6 % | 5,6 % | 4,3 % |
| 1,0 s | 11,7 % | 10,2 % | 1,8 % | 4,3 % | 3,1 % |
| **1,5 s** | **10,6 %** | **8,0 %** | **1,8 %** | **4,3 %** | **1,3 %** |
| 2,0 s | 11,1 % | 9,4 % | 1,8 % | 4,3 % | 1,3 % |
| 3,0 s | 7,8 % | 10,2 % | 1,8 % | 4,3 % | 1,3 % |

Presque toute l'erreur restante est de la parole attribuée à personne, pas à la mauvaise personne.

Un seuil de 0,3 au lieu de 0,5 gagnerait encore un peu : `grande-reunion` passerait de 4,3 à 1,9 %, et AMI 5 min de 10,6 à 9,2 %. Le seuil documenté par NVIDIA est gardé, pour ne pas régler Sténo sur des voix de synthèse.

## Vérification du portage

`backend/bench/nemotron_reference.py` compare le portage à transformers. Le modèle d'origine `nvidia/Nemotron-3-Diarization`, révision `f667ed7`, tourne dans un conteneur jetable (torch, transformers `c8b81b6`).

| Fichier | Export fp32 : trames décidées autrement | Export int8 (celui de l'image) |
|---|---|---|
| `dialogue` (81 s) | 0,00 % | 0,00 % |
| `grande-reunion` (146 s, plusieurs compressions de la mémoire) | 0,00 % | 0,07 % |
| AMI ES2004a (17,5 min, une quarantaine de compressions) | 0,00 % | 0,03 % |

`tests/test_nemotron.py` vérifie aussi, sans le modèle :

- le spectrogramme, contre 150 trames produites par transformers ;
- la mémoire des voix ;
- les tours de parole ;
- le choix du moteur.

## Réglage et repli

`DIARIZATION_ENGINE` vaut `nemotron` par défaut, ou `sherpa`. sherpa-onnx reste dans l'image. Il prend le relais dans deux cas :

- quand plus de 8 intervenants sont annoncés, car Nemotron en suit 8 au plus ;
- quand le modèle Nemotron manque.

Le banc compare les deux moteurs avec `--engine`.

## Limites

- **8 intervenants au plus.** Au-delà, sans nombre annoncé, des voix seront fusionnées. Avec le nombre annoncé, sherpa-onnx est utilisé.
- **Une ligne de transcription a toujours un seul intervenant**, celui qui parle le plus pendant la ligne. Nemotron détecte bien les paroles qui se chevauchent, mais la transcription ne les sépare pas encore. Cela relève de la phase 4 (deux pistes).
- **Langues** : NVIDIA ne cite pas le français parmi les langues d'entraînement. Les cas français du banc sont pourtant nettement meilleurs qu'avec sherpa-onnx.
- **Cas générés** : plusieurs voix du banc viennent du même modèle de synthèse (voir `docs/specs/banc-voix.md`), alors qu'AMI est une vraie réunion. Le gain sur AMI est plus modeste que sur les cas générés, mais il va dans le même sens.
- Le calcul se fait sur le processeur (onnxruntime sans CUDA) : 17 min d'audio prennent 10 s, et le GPU reste libre pour Whisper et Ollama.
