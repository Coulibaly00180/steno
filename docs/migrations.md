# Migrations de base de données

Le schéma PostgreSQL est géré par [Alembic](https://alembic.sqlalchemy.org/) (`backend/migrations/`). À chaque démarrage, le service Compose `migrate` (`python -m app.migrate`) met la base à jour **avant** l'API et le worker. Ceux-ci refusent de démarrer si la base n'est pas à la dernière révision.

## Commandes

`make` n'existe pas sous Windows : les commandes `docker compose` ci-dessous fonctionnent partout. Les cibles du `Makefile` donnent les mêmes là où `make` est installé.

| Effet | Commande | `make` |
|---|---|---|
| Sauvegarder la base, **avant toute mise à jour** | `docker compose exec -T postgres pg_dump -U videoai -Fc videoai > data/backups/videoai-<date>.dump` | `make backup` |
| Appliquer les migrations manquantes (sans effet si la base est à jour) | `docker compose run --rm migrate` | `make migrate` |
| Afficher la révision actuelle | `docker compose run --rm migrate alembic current` | `make db-current` |
| Générer une migration à partir des modèles | `docker compose -f compose.yaml -f compose.dev.yaml run --rm migrate alembic revision --autogenerate -m "description"` | `make db-revision m="description"` |

Restaurer une sauvegarde : voir [sauvegardes.md](sauvegardes.md). Le service `restore` garde une copie de la base actuelle et ne remplace rien en cas d'échec.

Les sauvegardes contiennent les transcriptions et les conversations : elles sont aussi sensibles que la base.

## Première mise à jour d'une installation existante

Les installations antérieures à Alembic ont été créées par `create_all`. Au premier démarrage :

- **Cas normal** — le schéma correspond à la révision de référence `0001`. Le journal de `migrate` indique « Base héritée conforme : révision 0001 posée ». Rien à faire, les données sont conservées. Une table entièrement absente (installation plus ancienne) est créée automatiquement.
- **Écart de schéma** — `migrate` s'arrête avec le code 2, liste les écarts et **n'écrit rien**. L'API et le worker ne démarrent pas. Deux options :
  1. corriger la base à la main pour qu'elle corresponde à `backend/migrations/versions/0001_baseline.py`, puis relancer `migrate` ;
  2. ou sauvegarder, repartir d'une base vide (`docker compose down -v`, qui **efface toutes les données**), redémarrer, puis réimporter les vidéos. L'historique est perdu.

Pour voir le journal : `docker compose logs migrate`.

## Écrire une migration

1. Modifier `backend/app/models.py`.
2. Générer la migration (commande ci-dessus) et **relire** le fichier produit.
3. Conventions :
   - `upgrade()` **et** `downgrade()` réels ;
   - nouvelle colonne sur une table existante : `nullable=True` ou `server_default` ;
   - contraintes et index nommés explicitement ;
   - données (valeurs initiales, renommages) dans une migration dédiée et idempotente ;
   - ne jamais importer `app.models` dans une migration.
4. Lancer les tests (`docker compose -f compose.test.yaml run --rm backend-tests`). `tests/test_migrations.py::test_upgrade_head_matches_models` échoue si les modèles et les migrations divergent.

## Retour arrière

- Code : revenir à l'image précédente.
- Schéma : `docker compose run --rm migrate alembic downgrade <révision>`. La révision `0001` ne se défait pas : restaurer une sauvegarde.
