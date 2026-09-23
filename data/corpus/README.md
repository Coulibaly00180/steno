# Corpus de référence (calibrage, phase 1 §19)

Cinq contenus sous licence libre ou réutilisable, choisis pour couvrir les cas d'usage : réunion, cours, podcast, conférence longue, clip court. Les fichiers médias et les résultats bruts restent locaux (`.gitignore`) ; seuls ce README, les scripts, `sources.json` et `references.json` sont versionnés.

| Clé | Contenu | Durée | Langue | Licence | Source |
|---|---|---|---|---|---|
| `reunion-fr` | Réunion en visioconférence du Volunteer Supporters Network : présentation de la recherche « Understanding Organizers' Impact on Newcomer Growth » (Wikimedia Foundation), puis échanges | 51 min | fr | CC BY-SA 4.0 | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:VSN_Skillshare_Understanding_Organizers%27_Impact_(French).webm) |
| `cours-fr` | Cours de culture numérique 2014-2015, séance 03 (parties A et B assemblées) : « Vidéo, entre télévision et internet », Hervé Le Crosnier, Université de Caen | 1 h 47 | fr | CC BY-SA | [Canal-U](https://www.canal-u.tv/chaines/cemu/cours-de-culture-numerique-2014-2015) |
| `podcast-en` | Democracy Now! du 27 décembre 2024 (titres, puis Gaza, Groenland, canal de Panama, Big Tech) | 59 min | en | CC BY-NC-ND 3.0 US | [Internet Archive](https://archive.org/details/dn2024-1227_vid) |
| `conference-en` | 38C3, salle ZIGZAG, 27 décembre 2024 : quatre conférences consécutives (*Breaking NATO Radio Encryption*, *What the PHUZZ?!*, *From fault injection to RCE*, *From Silicon to Sovereignty*) | 3 h 11 | en | CC BY 4.0 | [media.ccc.de](https://media.ccc.de/c/38c3) |
| `clip-fr` | « Définir un bien commun » | 3 min | fr | CC BY 3.0 | [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:D%C3%A9finir_un_bien_commun.ogv) |

Usage strictement non commercial et interne (tests), sans redistribution des médias ni des transcriptions.

## Méthode

- **Références** (`references.json`) : sujets tirés **uniquement** des descriptions officielles (résumés des conférences, sommaire de l'émission, descriptions Commons et Canal-U), jamais de nos transcriptions. Pour le cours, la référence est plus faible : le PDF des diapositives n'est plus en ligne, seuls les thèmes de la description sont utilisés.
- **Chapitres attendus** : débuts des conférences (`conference-en`) et des deux parties du cours (`cours-fr`), avec une tolérance de 2 minutes.
- **Exécution** : chaque contenu est importé par l'API de l'application en longueur standard, puis le résumé est régénéré en court et en détaillé. Les vidéos sont supprimées de l'application à la fin.

Résultats et décisions : `docs/specs/phase-1-resultats-justes.md`, section « Calibrage sur le corpus de référence ».

## Commandes

```sh
# Stack lancée (docker compose -f compose.yaml -f compose.gpu.yaml up -d --build). Téléchargement : voir l'historique dans docs/specs/phase-1-resultats-justes.md.
python data/corpus/evaluate.py [clé ...]   # écrit results.json (local)
python data/corpus/score.py                 # rapport Markdown : couverture, longueur/budget, chapitres
```
