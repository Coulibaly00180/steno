# Autonomie : glossaire qui apprend, sauvegardes, espace disque, dossier surveillé

| | |
|---|---|
| Statut | Implémenté (2026-09-23) |
| Origine | Feuille de route n° 2 : points 2, 13, 14 et 9 |
| Migration | `0009_storage_backups_learning` |

## n°2 — Glossaire qui apprend

- **Ce qui est appris** : chaque correction d'une ligne (`PATCH /videos/{id}/segments/{id}`) est comparée mot à mot à l'ancien texte (`difflib`). Chaque remplacement dont les nouveaux mots ressemblent à un terme (au moins une majuscule ou un chiffre : nom propre, sigle, produit) est enregistré dans `term_corrections` (« d'Oriana » → « Doñana »). Les mots en minuscules aux extrémités sont retirés (« de Doñana » → « Doñana »). Les corrections ordinaires (« vert » → « verre ») et les phrases réécrites (plus de 6 mots remplacés, ou plus de 4 mots de terme) sont ignorées. Un « Tout remplacer » compte pour autant d'occurrences que de remplacements.
- **Suggestion** : un terme corrigé au moins **2 fois**, absent du glossaire et jamais refusé, est proposé (`GET /glossary/suggestions`), avec les formes mal entendues et le nombre de vidéos. La graphie la plus saisie l'emporte.
- **En un clic** : « Ajouter » (`POST /glossary/suggestions/accept`) l'ajoute à la fin du glossaire global (limite de 300 termes : 409). « Ignorer » (`…/dismiss`) l'écarte définitivement (`glossary_dismissals`). Les suggestions apparaissent dans Paramètres › Glossaire et, pendant la correction d'une transcription, au-dessus des lignes (3 au plus).
- Les corrections survivent à la suppression de leur vidéo (`video_id` passe à NULL) : ce qui a été appris reste utile. Comme le glossaire, un terme ajouté ne sert qu'aux imports suivants.

## n°14 — Espace disque

- `GET /storage` : pour chaque vidéo, la taille réelle sur disque du fichier importé, de la piste de travail (WAV mono 16 kHz, ~115 Mo par heure) et des exports, triée par taille ; totaux, espace libre du disque, taille des sauvegardes. Paramètres › Espace disque les affiche.
- Actions sur une vidéo traitée (`POST /videos/{id}/storage`) ; le texte n'est jamais touché :
  - `audio` : job `COMPACT` du worker, qui remplace la source par une piste AAC mono 48 kbit/s (~20 Mo par heure, `.m4a`) et supprime le WAV. Lecture, sous-titres et identification des intervenants restent possibles. Refusé pour une source déjà compressée (MP3, M4A, Ogg, Opus). En cas d'échec, la source est conservée.
  - `delete_media` : source et WAV supprimés, immédiatement.
  - `delete_work_audio` : le WAV seul (refusé s'il est la seule piste de lecture).
- **À l'import** : « Après l'analyse : garder le fichier / garder seulement l'audio / supprimer les médias » (`source_policy` : `keep`, `audio`, `delete`). Le worker l'applique une fois la vidéo terminée (étape « Conversion en audio seul ») ; un échec garde les médias sans faire échouer la vidéo.
- Une vidéo sans média affiche « Les médias de cette vidéo ont été supprimés… » ; l'identification des intervenants est alors refusée (409).
- Défaut lié corrigé : l'image slim n'a pas de table MIME, les `.m4a`, `.mkv`, `.ogv`, `.flac`, `.ogg` étaient servis en `application/octet-stream`. Les types sont maintenant fixés.

## n°13 — Sauvegardes et archive portable

Voir [docs/sauvegardes.md](../sauvegardes.md).

- Nouveau service `scheduler` (`python -m app.scheduler`) : sauvegarde planifiée (`pg_dump -Fc`, réglages dans `app_settings`, rétention des seules sauvegardes `auto`), battement de cœur Redis visible dans l'état du système (« Dossier surveillé et sauvegardes »).
- `GET /backups`, `POST /backups` (maintenant), `GET|DELETE /backups/{nom}`, `PUT /settings/backups`. Les anciennes sauvegardes `videoai-*.dump` sont listées aussi.
- Restauration guidée : l'interface affiche les trois commandes à copier ; le service outil `restore` restaure dans une base temporaire puis bascule, après une copie de sécurité, et refuse tant qu'un service est connecté.
- Archive portable : `GET /library/export?media=true|false` (tar en flux), `POST /library/import` (fusion ; les vidéos déjà présentes sont ignorées). CLI : `python -m app.portable`.
- `postgresql-client` (version 17 dans Debian trixie, comme le serveur) est ajouté aux images backend.

## n°9 — Dossier surveillé

- Dossier `data/inbox` du projet. Le `scheduler` le parcourt toutes les 5 s (**polling** : les événements de fichiers ne traversent pas un montage Windows ou macOS). Un fichier dont la taille et la date n'ont pas changé depuis 10 s (`WATCH_STABLE_SECONDS`) est **déplacé** (pas copié) dans `data/uploads` et importé avec les réglages par défaut de Paramètres › Dossier surveillé : langues, template, longueur, glossaire, intervenants, règle des médias, tag facultatif. Mêmes validations que le formulaire d'import (code partagé : `import_settings` et `create_import` dans `main.py`).
- Ignorés : fichiers cachés, `~…`, téléchargements partiels (`.part`, `.crdownload`, `.tmp`…), `desktop.ini`, `Thumbs.db`, fichiers vides.
- Refus (format, durée, template supprimé…) : le fichier va dans `data/inbox/_rejets/` avec `<nom>.motif.txt`. Les Paramètres les listent avec « Réessayer » (retour dans le dossier) et « Supprimer ». Si un service est indisponible (Redis, base), le fichier reste dans le dossier et l'import est retenté une minute plus tard.
- La surveillance est désactivée par défaut.

## Vérifications

- 305 tests backend (46 nouveaux), dont un aller-retour réel `pg_dump` → modification → restauration sur PostgreSQL 17 + pgvector (base jetable), et la conversion ffmpeg réelle en `.m4a`. Frontend : `tsc` et `next build`.
- Sur la vraie pile GPU (médias de test à voix synthétique, supprimés ensuite) :
  - migration 0009 appliquée, première sauvegarde automatique écrite au démarrage du `scheduler` (lisible par `pg_restore --list`) ;
  - import avec « garder seulement l'audio » : 115 Ko → 78 Ko en `.m4a`, WAV supprimé, lecture dans le navigateur ;
  - dossier surveillé : une vidéo déposée est importée en ~14 s avec son tag et « supprimer les médias » appliqué ; un `.docx` est rangé dans `_rejets` avec son motif ;
  - « Tout remplacer » (3 occurrences) → suggestion, ajoutée d'un clic au glossaire ;
  - export de la bibliothèque puis ré-import : 3 vidéos ignorées, rien de modifié ;
  - `restore` lancé application démarrée : refusé, sans rien écrire.

## Limites

- La conversion `COMPACT` passe par la file principale : derrière une longue analyse, elle attend son tour.
- Les suggestions ne voient que les corrections faites à la main dans Sténo, pas les termes déjà bien transcrits.
- L'import d'archive par l'interface passe par le serveur web : au-delà de quelques Go, la ligne de commande est plus sûre.
- Le verrou entre deux sauvegardes simultanées (`flock`) peut être inopérant sur certains montages Docker Desktop : deux dumps simultanés coûtent seulement du temps, et la restauration vérifie elle-même les sessions ouvertes.
