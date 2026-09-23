# Migrations de base de données

Le schéma PostgreSQL est géré par [Alembic](https://alembic.sqlalchemy.org/) (`backend/migrations/`).
Le service Compose `migrate` (`python -m app.migrate`) met la base à jour à chaque `make up`, **avant** le démarrage de l'API et du worker. Ceux-ci refusent de démarrer si la base n'est pas à la dernière révision.

## Commandes

| Commande | Effet |
|---|---|
| `make backup` | Sauvegarde la base dans `data/backups/videoai-<date>.dump` (à faire avant toute mise à jour) |
| `make migrate` | Applique les migrations manquantes (sans effet si la base est à jour) |
| `make db-current` | Affiche la révision actuelle de la base |
| `make db-revision m="description"` | Génère une nouvelle migration à partir des modèles (overlay dev : le fichier est créé dans `backend/migrations/versions/`) |

Restaurer une sauvegarde :

```sh
docker compose exec -T postgres pg_restore -U videoai -d videoai --clean --if-exists < data/backups/videoai-<date>.dump
```

Les sauvegardes contiennent les transcriptions et les conversations : elles sont aussi sensibles que la base.

## Première mise à jour d'une installation existante

Les installations antérieures à Alembic ont été créées par `create_all`. Au premier `make up` :

- **Cas normal** — le schéma correspond à la révision de référence `0001` : le log `migrate` indique « Base héritée conforme : révision 0001 posée ». Rien à faire ; les données sont conservées. Une table entièrement absente (installation plus ancienne) est créée automatiquement.
- **Écart de schéma** — `migrate` s'arrête avec le code 2, liste les écarts et **n'écrit rien**. L'API et le worker ne démarrent pas. Deux options :
  1. corriger la base à la main pour qu'elle corresponde à `backend/migrations/versions/0001_baseline.py`, puis `make migrate` ;
  2. ou `make backup`, `make reset`, `make up`, puis réimporter les vidéos (l'historique est perdu).

Pour voir les logs : `docker compose logs migrate`.

## Écrire une migration

1. Modifier `backend/app/models.py`.
2. `make db-revision m="ajout summary_length"` et **relire** le fichier généré.
3. Conventions :
   - `upgrade()` **et** `downgrade()` réels ;
   - nouvelle colonne sur une table existante : `nullable=True` ou `server_default` ;
   - contraintes et index nommés explicitement ;
   - données (seeds, renommages) dans une migration dédiée et idempotente ;
   - ne jamais importer `app.models` dans une migration.
4. `pytest tests/test_migrations.py` : `test_upgrade_head_matches_models` échoue si le modèle et les migrations divergent.

## Retour arrière

- Code : revenir à l'image précédente.
- Schéma : `docker compose run --rm migrate alembic downgrade <révision>`. La révision `0001` ne se défait pas (restaurer une sauvegarde).
