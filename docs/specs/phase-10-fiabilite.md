# Fiabilité : mots douteux, résumé vérifiable, actions et décisions, exports

| | |
|---|---|
| Statut | Implémenté (2026-09-24) |
| Origine | Feuille de route n° 2 : points 1, 3, 5 et 8 |
| Migration | `0012_doubts_sources_actions` |

## n°1 — Mots douteux

- **Mesure** : faster-whisper transcrit avec `word_timestamps=True`, ce qui donne une probabilité par mot. Un mot est « douteux » sous 0,5 (`DOUBT_PROBABILITY`, `app/transcription.py`). Les mots de moins de 2 caractères et les mots-outils (« le », « de », « et »…) sont ignorés : leurs erreurs ne changent pas le sens.
- **Seuil mesuré** sur une vidéo réelle : 2,2 % des mots sont sous 0,5 et 9 % sous 0,8. Au-delà de 0,5, on soulignerait une ligne sur deux.
- **Limite connue** : large-v3-turbo est trop sûr de lui sur une voix de synthèse. Sur la réunion de test, des noms mal entendus avaient une probabilité de 0,68 à 0,91. Un mot non souligné n'est donc pas garanti juste.
- **Stockage** : `transcript_segments.doubts` contient une liste JSON `[début, fin, probabilité en %]`. Les positions sont des caractères dans le texte du segment. La liste est effacée quand l'utilisateur corrige le segment ou fait un « remplacer tout ».
- **Interface** : les mots douteux sont soulignés d'une vague orange, avec la probabilité en info-bulle. Le bouton « Prochain doute i/N » passe d'un mot au suivant, et le nombre de mots incertains s'affiche à côté.

## n°3 — Résumé vérifiable

- `GET /videos/{id}/summaries/{sid}/sources` (`app/verification.py`) relie chaque ligne du résumé à son passage le plus proche dans la transcription, par le sens (bge-m3, index de la recherche sémantique). Seules les lignes de 3 mots ou plus sont traitées.
- **Seuil de 0,40** en distance cosinus, mesuré sur la réunion de test :

  | Lignes | Distance |
  |---|---|
  | Fidèles à la transcription | 0,31 à 0,38 |
  | Ajoutées (inventées) | 0,43 à 0,60 |

- Un horodatage cité par le résumé (`[12:34]`) est comparé au passage trouvé.
- Une ligne sous le seuil affiche une puce « ▸ horodatage » qui ouvre le passage. Au-dessus, elle est surlignée et marquée « à vérifier ». Le bouton « Sources · N à vérifier » affiche ou masque ces repères.
- **Limite** : la vérification compare le sujet, pas les détails. Un faux détail sur un sujet réellement abordé passe. Par exemple, « Katana OLED » au lieu de « Katana » ressort à 0,38. La puce indique « le passage qui traite de ce point », pas « c'est prouvé ».
- **Cache** : le résultat est stocké dans `summaries.sources`. La clé est une empreinte du résumé, du modèle d'embeddings et de la transcription : une correction de la transcription relance le calcul. Tant que la vidéo n'est pas indexée, la réponse est `status: "indexing"`.

## n°5 — Actions et décisions

- **Extraction** (`app/actions.py`) : après chaque résumé (étape `EXTRACTING_ACTIONS`, progression 94 %), le LLM lit le résumé et, s'ils tiennent dans le contexte, les résumés de blocs. Il renvoie des actions et des décisions au format imposé par un schéma JSON : texte, responsable, échéance en mots, horodatage. Une erreur à cette étape n'interrompt jamais l'analyse.
- **Dates calculées par le code**. Le modèle datait « fin du mois », dit le 24/09, au 31/10. `date_from_words` part de la date de la réunion et reconnaît :
  - aujourd'hui, demain, après-demain ;
  - fin de semaine, semaine prochaine, fin du mois, fin du mois prochain ;
  - « dans N jours » ou « dans N semaines » ;
  - les jours de la semaine (« lundi prochain ») ;
  - « 15 octobre », reporté à l'année suivante si la date est passée ;
  - « 15/10 ».

  La date du modèle ne sert que si le code ne sait pas lire l'échéance. Sans mots d'échéance, il n'y a pas de date du tout.
- **Nouvelle extraction** : seules les actions automatiques, ouvertes et non modifiées sont remplacées. Les actions ajoutées, modifiées, faites ou abandonnées par l'utilisateur restent, et les doublons sont écartés.
- **Table `action_items`** : type (action ou décision), texte, responsable, échéance en mots et en date, statut (à faire, faite, abandonnée), horodatage, origine (automatique ou manuelle), indicateur de modification.
- **API** :
  - `GET /videos/{id}/actions` et `POST /videos/{id}/actions` ;
  - `PATCH /actions/{id}` et `DELETE /actions/{id}` ;
  - `POST /videos/{id}/actions/extract` relit le résumé ;
  - `GET /actions` interroge toute la bibliothèque (filtres statut, type, responsable, texte).
- **Exports** :
  - `GET /actions/export.csv` : UTF-8 avec BOM et séparateur « ; », pour qu'Excel l'ouvre tel quel ;
  - `GET /actions/export.ics` : un événement « journée entière » par action ouverte et datée. Le format est RFC 5545, avec des lignes repliées à 75 octets.
- **Interface** :
  - panneau « Actions et décisions » sous le résumé : cocher, modifier, abandonner, supprimer, ajouter, relire le résumé, exporter en CSV ou .ics ;
  - page « Actions » pour toute la bibliothèque, avec filtres et échéances (« dans 4 j », « en retard »).

## n°8 — Exports vers Obsidian et l'e-mail

- **Note Markdown** (`GET /videos/{id}/note.md`, `app/notes.py`), dans cet ordre :
  - en-tête YAML : titre, date, durée, langues, source, tags, personnes en `[[liens]]`, identifiant Sténo ;
  - le compte-rendu, avec ses titres descendus de deux niveaux sous « ## Compte-rendu » ;
  - les actions en cases à cocher (responsable, 📅 date) et les décisions ;
  - les chapitres, puis les fiches citées ;
  - la transcription, sur option (`?transcript=true`).
- **Bibliothèque pour Obsidian** : `GET /library/export/obsidian.zip`, avec le bouton « Exporter pour Obsidian » dans les Paramètres, section Sauvegardes.
  - Contenu : un dossier « Sténo » avec « Vidéos », « Personnes », « Organisations », « Lieux » et « Dates ». Chaque fiche liste ce qui est dit d'elle, vidéo par vidéo.
  - L'archive est produite au fil de l'eau, vidéo par vidéo, sans la garder en mémoire.
  - Deux vidéos de même titre sont distinguées par leur date.
- **Brouillon d'e-mail** : `GET /videos/{id}/email.eml`. Outlook et Thunderbird l'ouvrent comme un message à envoyer (`X-Unsent: 1`).
  - Contenu : le compte-rendu, un « Suivi des actions » avec responsables et dates, et les décisions.
  - Le message a une partie texte et une partie HTML. Les titres Markdown y deviennent de vrais titres.
- Ces deux routes ne sont pas sous `/exports/{name}`, qui sert les fichiers produits par le worker et répondrait en premier.
