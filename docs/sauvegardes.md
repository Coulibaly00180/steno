# Sauvegardes, restauration et archive de la bibliothèque

## Ce qui est sauvegardé

- **Sauvegarde de la base** (`data/backups/steno-AAAAMMJJ-HHMMSS-<type>.dump`) : un `pg_dump -Fc` complet (transcriptions, traductions, résumés, conversations, glossaire, templates, réglages). Types : `auto` (planifiée), `manuel` (bouton « Sauvegarder maintenant » ou `python -m app.backups create`), `avant-restauration` (copie de sécurité prise juste avant une restauration).
- **Les médias ne sont pas dans la sauvegarde** : ils restent dans `data/uploads` et `data/audio`. Copier régulièrement tout le dossier `data/` sur un autre disque protège l'ensemble (base comprise, puisque les sauvegardes sont dans `data/backups`).

## Planification

Le service `scheduler` écrit une sauvegarde automatique au démarrage si aucune n'existe, puis selon la fréquence choisie dans Paramètres › Sauvegardes (par défaut : chaque jour, 7 conservées). Seules les sauvegardes `auto` sont élaguées ; les autres ne sont supprimées que par l'utilisateur. Un échec est affiché dans les Paramètres et retenté au bout de 15 minutes.

## Restaurer une sauvegarde

La restauration **remplace** la base : elle se lance en ligne de commande, application arrêtée.

```sh
docker compose stop api worker scheduler web
docker compose --profile tools run --rm restore steno-20260923-030000-auto.dump
docker compose -f compose.yaml -f compose.gpu.yaml up -d   # ou : docker compose up -d
```

(`make restore f=<fichier>` fait les trois étapes là où `make` existe.)

Le service `restore` :

1. refuse de continuer si une autre session utilise la base (un service oublié) ;
2. écrit une copie de sécurité `…-avant-restauration.dump` de la base actuelle ;
3. restaure dans une base temporaire `videoai_restauration` : en cas d'échec, elle est supprimée et la base actuelle reste intacte ;
4. remplace la base par la base restaurée.

Au redémarrage, le service `migrate` met à jour le schéma si la sauvegarde est plus ancienne que le code. Les vidéos importées après la date de la sauvegarde disparaissent de la bibliothèque, mais leurs fichiers restent dans `data/uploads`.

## Archive portable de la bibliothèque

Pour déplacer ses analyses vers une autre installation, ou fusionner deux bibliothèques : Paramètres › Sauvegardes › « Exporter la bibliothèque » (option « Inclure les médias »), puis « Importer une archive » de l'autre côté.

- Format : un `.tar` non compressé (`steno-library.json`, `glossary.json`, `templates.json`, `videos/<id>/video.json` et, en option, `videos/<id>/source.<ext>`, puis `conversations.json`). Il est produit en flux, sans fichier temporaire.
- L'import **ajoute** : les vidéos déjà présentes (même identifiant) sont ignorées, les templates sont ajoutés s'ils manquent (par nom), les termes du glossaire sont fusionnés. Seules les vidéos traitées sont exportées. L'index des questions et les exports sont reconstruits après l'import.
- Une grosse archive (médias compris) s'importe plutôt en ligne de commande, après l'avoir copiée dans `data/` :

```sh
docker compose exec api python -m app.portable export --media /data/exports/bibliotheque.tar
docker compose exec api python -m app.portable import /data/bibliotheque.tar
```
