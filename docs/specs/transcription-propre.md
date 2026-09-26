# Une transcription propre : plus de boucles ni de phrases inventées

| | |
|---|---|
| Statut | Livré (2026-09-26) |
| Origine | Feuille de route n° 3 : phase 2 |
| Migration | aucune |

## Le problème

Whisper, avec le réglage par défaut de faster-whisper, relit le texte de la fenêtre précédente avant de décoder la suivante (`condition_on_previous_text=True`). Sur le corpus, il s'accrochait à une ligne et la répétait :

- **clip-fr** : 6 « Merci. » d'une seconde exactement, à la suite, sur le générique musical de fin ;
- **reunion-fr** : 12 « Merci. » isolés, dont 2 pile à 600 s, à la jonction de deux fenêtres de 10 minutes.

D'autres ont fait le même constat : le ticket #20 et la PR #8 d'omarchy-meeting-recorder, où une boucle avait même avalé une minute de vraie parole.

## Ce qui change

### 1. Plus de reprise du texte précédent

`transcribe_windows` passe `condition_on_previous_text=False`. Chaque morceau est décodé sans le texte du précédent.

### 2. Le vocabulaire passé aussi en `hotwords`

Sans reprise, faster-whisper n'applique `initial_prompt` qu'aux 30 premières secondes de chaque appel (vérifié dans son code : `prompt_reset_since`). Les `hotwords`, eux, s'appliquent à chaque fenêtre. Le glossaire passe donc par les deux (`whisper_hotwords` dans `analysis_options.py`).

Mesure : une réunion française de synthèse de 15,5 minutes, 48 occurrences de trois termes rares, découpée en fenêtres de 10 minutes comme dans Sténo.

| Réglage | Xylophonics | Quotespec | Benally |
|---|---|---|---|
| Sans vocabulaire | 6/16 | 0/16 | 0/16 |
| Avant : prompt, avec reprise | 16/16 | 16/16 | 16/16 |
| Prompt seul, sans reprise | 6/16 | 2/16 | 1/16 |
| `hotwords` seuls, sans reprise | 16/16 | 15/16 | 16/16 |
| **Livré : prompt + `hotwords`, sans reprise** | **16/16** | **16/16** | **16/16** |

Désactiver la reprise seule, comme le suggérait le ticket #20, aurait donc cassé le glossaire.

### 3. Un filtre étroit pour ce qui resterait

Le filtre est dans `transcription.py` :

- **crédits de sous-titres** (« Sous-titres réalisés par la communauté d'Amara.org », « Sous-titrage ST' 501 », « Subtitles by… ») : toujours retirés, personne ne les prononce en réunion ;
- **formules toutes faites** (« Merci. », « Merci d'avoir regardé », « Thank you for watching ») : retirées seulement quand Whisper doutait qu'il y ait de la parole (`no_speech_prob` supérieur à 0,5). Un vrai « merci d'avoir regardé » à la fin d'une conférence reste ;
- **boucles dans une ligne** : un groupe de 1 à 6 mots répété au moins 4 fois de suite n'est gardé qu'une fois. « Non, non, non » (3 fois) reste ;
- **lignes identiques** : au-delà de 2 à la suite, les suivantes sont retirées. Deux « Merci. » peuvent venir de deux personnes, six sont une boucle.

`TRANSCRIPTION_VERSION` (2) entre dans l'empreinte et dans le cache du suivi de qualité : le corpus a donc été retranscrit.

## Mesures sur le corpus

| Fichier | Lignes répétées | « Merci. » isolés | Mots (avant → après) |
|---|---|---|---|
| clip-fr | 5 → **0** | 6 → **0** | 369 → 362 : seuls les « Merci. » inventés disparaissent |
| reunion-fr (51 min) | 8 → **0** | 12 → **0** | 3 924 → 3 900 |
| podcast-en (59 min) | 1 → **0** | 0 → 2 (de vrais « Thank you. ») | 8 580 → 8 416 |

- **Ce qui disparaît sur le podcast** : surtout des bégaiements (« it's it's it's it's », « this is this is ») et une reprise en double de la phrase d'annonce de l'émission. Un petit fragment réel part aussi (« glass of water or a »).
- **Le « Merci. » réel de reunion-fr à 296 s est conservé**, suivi de « Combien de temps j'ai pour la présentation ? ». Les deux sont désormais dans une ligne plus longue (287,5 à 301 s) : sans reprise du contexte, Whisper découpe parfois des lignes un peu plus longues.
- **Évaluation de qualité** : 65,9 % contre 56,8 % auparavant. Le podcast passe de 5 à 8 sujets couverts. La longueur du résumé de clip-fr passe de 69 % à 49 % du budget : c'est une seule évaluation, et le résumé varie d'une fois à l'autre (voir la transcription par lots).

## Reste à discuter

La densité des chapitres : environ 3 × √(minutes) aujourd'hui, soit 21 pour la réunion de 51 minutes. omarchy-meeting-recorder vise un chapitre toutes les 5 à 10 minutes. C'est une question d'usage, pas de mesure.
