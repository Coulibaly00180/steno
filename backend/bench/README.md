# Banc « voix »

Ce banc mesure deux choses : qui parle (identification des intervenants) et ce que Whisper entend. Il tourne sur des réunions françaises générées et sur une vraie réunion enregistrée (AMI). L'intégration continue le lance à chaque modification et échoue sous les seuils de `thresholds.json`.

Origine : feuille de route n° 3, phase 1 (`docs/roadmap.md`) ; mesure de départ dans `docs/specs/banc-voix.md`, passage à Nemotron dans `docs/specs/nemotron.md`.

## Lancer

```sh
docker compose -f compose.test.yaml run --rm bench          # les cas français + 5 min d'AMI, avec vérification des seuils
MSYS_NO_PATHCONV=1 docker compose -f compose.test.yaml run --rm bench python -m bench.run --case --ami --ami-minutes 0   # AMI en entier (17 min)
MSYS_NO_PATHCONV=1 docker compose -f compose.test.yaml run --rm bench python -m bench.run --speakers-given --json /cache/bench/scores.json
MSYS_NO_PATHCONV=1 docker compose -f compose.test.yaml run --rm bench python -m bench.run --ami --engine sherpa   # l'ancien moteur, pour comparer
```

Options de `bench.run` :

| Option | Effet |
|---|---|
| `--case NOM…` | seulement ces cas ; `--case` sans nom : aucun cas généré (AMI seul) |
| `--ami` | ajoute la réunion AMI ES2004a |
| `--ami-minutes N` | durée d'AMI prise en compte (5 par défaut, 0 pour les 17 min) |
| `--model NOM` | modèle Whisper (par défaut `WHISPER_MODEL`, `small` dans la pile de test) |
| `--speakers-given` | mesure aussi l'erreur quand le nombre d'intervenants est donné |
| `--mixed` | cas à deux côtés : pistes mélangées d'abord, comme avant la phase 4 |
| `--engine nemotron\|sherpa` | moteur d'identification des voix (par défaut `DIARIZATION_ENGINE`, Nemotron) |
| `--json FICHIER` | écrit les résultats, pour comparer deux versions |
| `--check` | code de sortie 1 sous les seuils |

Ce qui dure longtemps est téléchargé une seule fois dans `.bench-cache/` (ignoré par git) : le modèle Whisper et la réunion AMI.

Le service `bench` utilise l'image de l'application, parce que les modèles d'identification des voix y sont intégrés. Le code est monté : aucune reconstruction n'est nécessaire entre deux lancements.

## Les colonnes

| Colonne | Ce qu'elle mesure |
|---|---|
| **erreur de personne** | Part de la parole attribuée au mauvais intervenant, ou à personne. Mesurée par tranches de 10 ms, sur les seuls moments où une personne parle, après la meilleure correspondance un à un entre les étiquettes trouvées et les vraies personnes. |
| **voix** | Intervenants trouvés sur intervenants présents. |
| **mots** | Mots du script retrouvés dans la transcription, accents ignorés (cas français). |
| **bonne personne** | Mots de la transcription attribués à la bonne personne. |
| **lignes** | Nombre de lignes de la transcription. Dans le cas « silence », il doit être à 0 : aucune phrase inventée. |
| **bon côté** | Cas à deux côtés : mots de la transcription mis du bon côté (le vôtre ou celui des autres). |
| **lignes d'écho** | Cas à deux côtés : lignes mises de votre côté alors que seul l'autre côté parlait (sa voix revenue par les haut-parleurs). Doit être à 0. |

## Les cas

| Cas | Ce qu'il teste |
|---|---|
| `dialogue` | Deux personnes, avec des « d'accord » qui se chevauchent |
| `reunion` | Quatre personnes, avec des interruptions |
| `grande-reunion` | Six personnes, dont deux ne parlent qu'environ 15 s. C'était la limite sous laquelle sherpa-onnx rattachait une voix à une autre. |
| `musique` | Deux personnes avec une musique de fond |
| `bruit` | Deux personnes avec un micro bruyant (bruit rose) |
| `silence` | 20 s de bruit de pièce, sans parole |
| `appel` | Une visioconférence en stéréo, comme un enregistrement « Les deux » : Claire au micro (gauche), Pierre et Sophie de l'autre côté (droite), avec un casque |
| `appel-haut-parleurs` | La même, sans casque : l'autre côté revient dans le micro, 60 ms plus tard, au tiers de son niveau, étouffé |
| `ami-ES2004a` | Une vraie réunion à quatre, en anglais : 5 minutes par défaut, 17 avec `--ami-minutes 0` |

Chaque cas de `cases/` contient :

- `audio.ogg` : Opus, 16 kHz, mono (stéréo pour les cas à deux côtés) ;
- `truth.json` : chaque réplique avec son auteur, son début, sa fin et son texte ; pour les cas à deux côtés, le côté de chaque personne (`sides`).

## Générer de nouveaux cas

Les scripts sont dans `scripts/`. Une ligne s'écrit `personne|pause|texte`. La pause est le nombre de secondes après la fin de la réplique précédente ; une pause négative fait parler deux personnes en même temps.

Pour régénérer les cas (les fichiers produits sont versionnés ; des noms de cas après `generate.py` ne produisent que ceux-là) :

```sh
MSYS_NO_PATHCONV=1 docker run --rm -v "<dépôt>\\backend\\bench:/bench" python:3.12-slim sh -c "apt-get update -qq && apt-get install -y -qq ffmpeg >/dev/null && pip install -q piper-tts numpy && python /bench/generate.py"
```

Voix et licences : `NOTICE.md`.

## Vérifier le portage de Nemotron

`nemotron_reference.py` compare `app/nemotron.py` à l'implémentation de Hugging Face transformers, sur `dialogue`, `grande-reunion` et AMI en entier. À relancer si le modèle, sa révision ou le portage changent : mode d'emploi en tête du fichier.

## Limites

- Plusieurs personnes partagent un même modèle de synthèse : Pierre et Sophie viennent tous deux d'upmc ; Karim, Marc et Lina de mls. Elles peuvent donc se ressembler plus que de vraies voix. La réunion AMI, enregistrée pour de vrai, sert de contrepoint.
- Les cas générés sont en français, AMI en anglais.
