# Spécification technique — Phase 0 : Socle technique

| | |
|---|---|
| Statut | Implémenté (2026-09-22) — voir « Écarts d'implémentation » en fin de document |
| Date | 2026-09-22 |
| Périmètre | Migrations de schéma (Alembic) · Ordre déterministe des résumés et segments · État du système par service |
| Débloque | Phase 1 (`docs/specs/phase-1-resultats-justes.md`) et toutes les phases qui modifient le schéma |
| Estimation | ~4–5 jours, 1 développeur |

---

## 1. Résumé

La phase 0 ne livre aucune fonctionnalité visible, sauf un état du système enfin fiable. Elle rend possibles les évolutions suivantes :

1. **Migrations versionnées** : Alembic remplace `Base.metadata.create_all`. Un service Compose `migrate`, lancé une seule fois, met la base à jour avant le démarrage de l'API et du worker. Les installations existantes sont reprises automatiquement (marquage de la révision initiale), sans perte de données.
2. **Ordre déterministe** : les relations `Video.summaries` et `Video.segments` sont triées explicitement. Le « dernier résumé » (`summaries[-1]`) devient fiable, ce que le n°9 (plusieurs résumés par vidéo) rend indispensable.
3. **État du système réel** : un nouvel endpoint `GET /status` vérifie séparément la base, Redis, le worker, Ollama et la présence du modèle LLM. Le panneau « État du système » affiche enfin un statut par service.

Principe : le plus petit changement d'architecture possible. Pas de nouvelle dépendance d'infrastructure, une seule dépendance Python (`alembic`), un service Compose éphémère.

## 2. Existant

| Élément | État actuel | Fichier |
|---|---|---|
| Création du schéma | `init_db()` → `Base.metadata.create_all()`, qui crée les tables absentes mais **ne modifie jamais** une table existante | `backend/app/db.py:14` |
| Appels à `init_db()` | Au démarrage de l'API (`lifespan`), au démarrage du worker, **et au début de chaque traitement** | `main.py:46`, `worker_entry.py:16`, `worker.py:200` |
| Migrations | Aucune ; pas d'Alembic | `requirements.txt` |
| Tests | SQLite + `create_all` (`conftest.py`) ; un job CI PostgreSQL pour la concurrence (`create_all` aussi) | `backend/tests/`, `.github/workflows/ci.yml` |
| Relations | `segments`, `summaries`, `chat_messages` sans `order_by` | `models.py:27-29` |
| Lecture du dernier résumé | `video.summaries[-1]` (chat) et `summaries.at(-1)` (frontend) | `main.py:363`, `videos/[id]/page.tsx:113` |
| Préparation (`/ready`) | `SELECT 1` + `PING` Redis ; sert au healthcheck Docker de l'API, dont dépend le service `web` | `main.py:77`, `compose.yaml` |
| Panneau « État du système » | Interroge `/api/ready` toutes les 30 s et colore **les 5 services** avec ce seul résultat. Le worker et Ollama ne sont jamais vérifiés | `frontend/components/AppShell.tsx` |
| Worker | Un seul worker RQ, file `video-ai` | `worker_entry.py` |

**Conséquences :**
- Toute colonne ajoutée au modèle est ignorée sur une base existante : l'application plante à la première requête qui la lit.
- Sans `ORDER BY`, PostgreSQL ne garantit aucun ordre : les segments et résumés sont renvoyés dans l'ordre physique, en général celui d'insertion, mais rien ne le garantit (après un `VACUUM FULL` ou des mises à jour, par exemple).
- Un worker arrêté ou un modèle non téléchargé (`make pull-model` oublié) s'affichent « Opérationnel ». L'utilisateur voit seulement sa vidéo rester « En attente » indéfiniment.

## 3. Architecture proposée

```
                 ┌──────────┐
                 │ postgres │  (healthy)
                 └────┬─────┘
                      │
                 ┌────▼─────┐   python -m app.migrate
                 │ migrate  │   verrou consultatif → reprise de l'existant → upgrade head
                 └────┬─────┘   (s'arrête avec le code 0)
          ┌───────────┴───────────┐
   ┌──────▼──────┐         ┌──────▼──────┐
   │     api     │         │   worker    │   au démarrage : vérifie que la
   │ (vérif. du  │         │ (vérif. du  │   révision en base = head, sinon
   │  schéma)    │         │  schéma)    │   refuse de démarrer
   └──────┬──────┘         └─────────────┘
          │  GET /status → base · redis · worker (registre RQ) · ollama · modèle
   ┌──────▼──────┐
   │     web     │  panneau État du système : un statut par service
   └─────────────┘
```

Choix structurants :
- **Les migrations ne tournent jamais dans l'API ni dans le worker**, uniquement dans `migrate`. L'API et le worker se contentent de **vérifier** la révision.
- **`/ready` ne change pas** : il reste le healthcheck Docker (base + Redis). Ollama ou le worker indisponibles ne doivent pas rendre l'API « unhealthy », sinon `web` ne démarrerait pas et l'utilisateur ne verrait même pas le diagnostic.
- **Le worker n'a pas de heartbeat maison** : on lit le registre de workers que RQ maintient déjà dans Redis.

## 4. Changements par composant

### 4.1 Backend — nouveaux fichiers

```
backend/
├── alembic.ini
├── migrations/
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
│       └── 0001_baseline.py
└── app/
    ├── migrate.py        # point d'entrée du service migrate
    ├── schema.py         # vérification de révision (API/worker)
    └── status.py         # vérifications de /status
```

**`migrations/env.py`**
- `target_metadata = Base.metadata` (import de `app.models` pour enregistrer toutes les tables).
- URL issue de `app.config.settings.database_url` : une seule source de configuration, rien dans `alembic.ini`.
- `compare_type=True` (pas `compare_server_default` : les modèles n'ont que des défauts côté Python, et ce réglage produirait de faux écarts).
- `render_as_batch=True` quand le dialecte est SQLite (tests), pour que les futurs `ALTER` fonctionnent aussi.
- `transaction_per_migration=True` : une migration qui échoue n'annule pas les précédentes déjà réussies.

**`0001_baseline.py`** : reproduit **exactement** le schéma actuel (5 tables, index `ix_*_video_id`, contrainte unique `summary_templates.name`, clés étrangères `ON DELETE CASCADE`). Il est généré par `alembic revision --autogenerate` sur une base vide, puis relu à la main. `downgrade()` lève une erreur explicite : on ne supprime pas toute la base par un downgrade.

**Pas de `naming_convention`** sur `Base.metadata` en phase 0 : elle changerait les noms de contraintes attendus par rapport à ceux que PostgreSQL a déjà créés (`summary_templates_name_key`, `*_pkey`), et la comparaison de la reprise échouerait. Les migrations futures nommeront explicitement leurs contraintes (par exemple l'index partiel du template par défaut en phase 1).

**`app/migrate.py`** — algorithme :

```text
1. Connexion à la base (5 tentatives, attente croissante — postgres est healthy mais peut refuser brièvement).
2. PostgreSQL : pg_advisory_lock(<clé fixe>) → sérialise deux `migrate` concurrents (par exemple deux `docker compose run`).
3. Inspecter la base :
   a. Aucune table applicative et pas de alembic_version → base neuve → upgrade head.
   b. Tables applicatives présentes et pas de alembic_version → base héritée (create_all) :
        - comparer le schéma réel au schéma de 0001 (alembic.autogenerate.compare_metadata
          avec la metadata figée de la révision 0001) ;
        - aucune différence → stamp 0001 puis upgrade head ;
        - différences → ÉCHEC, code de sortie 2, liste des écarts dans les logs,
          aucune écriture en base.
   c. alembic_version présent → upgrade head (sans effet si déjà à jour).
4. Libérer le verrou, journaliser « révision avant → après », sortir avec le code 0.
```

Pour que la comparaison de l'étape 3b reste stable quand les modèles évolueront (phase 1 et suivantes), `0001_baseline.py` expose une fonction `baseline_metadata()` qui construit la metadata **figée** de la révision 0001, indépendamment de `app.models`.

**`app/schema.py`**

```python
def assert_schema_current(engine) -> None:
    """Refuse to run against a database that is not at the migration head."""
    # MigrationContext.get_current_revision() vs ScriptDirectory.get_current_head()
    # mismatch → raise SchemaOutOfDate("Base à la révision X, attendu Y : lancez `make migrate`")
```

### 4.2 Backend — fichiers modifiés

| Fichier | Changement |
|---|---|
| `requirements.txt` | + `alembic` (version épinglée, dernière 1.x au moment de l'implémentation) |
| `app/db.py` | `init_db()` est **supprimé** du code de production. Une fonction `create_all_for_tests(engine)` reste utilisable par les tests unitaires |
| `app/main.py` | `lifespan` : `init_db()` → `assert_schema_current(engine)`. Nouvel endpoint `GET /status` |
| `app/worker_entry.py` | `init_db()` → `assert_schema_current(engine)` avant `worker.work()`. Paramètre `worker_ttl` réduit (voir §8) |
| `app/worker.py` | Suppression de `init_db()` au début de `run_pipeline` (`worker.py:200`) : ce `create_all` par traitement est inutile et coûteux |
| `app/models.py` | `segments` : `order_by=(TranscriptSegment.start_seconds, TranscriptSegment.id)` ; `summaries` : `order_by=Summary.created_at` ; `chat_messages` : `order_by=VideoChatMessage.created_at` |
| `app/main.py:363` | Inchangé : `summaries[-1]` devient correct grâce au tri. Un commentaire indique qu'il dépend de l'`order_by` du modèle |

`order_by` ne touche pas au schéma : **aucune migration** n'est nécessaire pour ce point. Les index existants sur `video_id` suffisent aux volumes actuels, où le tri se fait en mémoire après filtrage.

### 4.3 Infrastructure

**`compose.yaml`**

```yaml
  migrate:
    build: ./backend
    command: python -m app.migrate
    env_file: .env
    restart: "no"
    depends_on:
      postgres:
        condition: service_healthy

  api:
    depends_on:
      migrate:
        condition: service_completed_successfully
      # postgres, redis, ollama : inchangés

  worker:
    depends_on:
      migrate:
        condition: service_completed_successfully
      # postgres, redis, ollama : inchangés
```

- `compose.gpu.yaml` : aucun changement, `migrate` utilise l'image CPU standard.
- `compose.dev.yaml` : `migrate` monte `./backend:/app` comme `api` et `worker`, pour exécuter les migrations en cours d'écriture.

**`Makefile`**

| Cible | Commande |
|---|---|
| `migrate` | `docker compose run --rm migrate` |
| `db-revision` | `docker compose run --rm migrate alembic revision --autogenerate -m "$(m)"` |
| `db-current` | `docker compose run --rm migrate alembic current` |
| `backup` | `docker compose exec -T postgres pg_dump -U videoai -Fc videoai > data/backups/videoai-<horodatage>.dump` |

`data/backups/` est ajouté au `.gitignore`.

### 4.4 Frontend

| Fichier | Changement |
|---|---|
| `components/AppShell.tsx` | Interroge `/api/status` au lieu de `/api/ready`. Un statut et un détail par service. Pastille : verte si tout est `ok`, orange si au moins un service est `degraded` ou si le worker, Ollama ou le modèle sont `down`, rouge si l'API ne répond pas |
| `app/page.tsx` | Bandeau non bloquant au-dessus du formulaire si `worker` ou `model` ne sont pas `ok` : « Aucun worker actif : les analyses resteront en attente » / « Modèle qwen3:8b absent : lancez `make pull-model` ». L'import reste possible |

Libellés du panneau : API, Base de données, Redis, Worker (« inactif », « occupé », « arrêté »), Ollama, Modèle LLM (« qwen3:8b · présent » ou « absent »).

## 5. Flux de données

**Démarrage :** postgres healthy → `migrate` (verrou, reprise éventuelle, upgrade, code 0) → `api` et `worker` démarrent → chacun compare la révision en base au head embarqué dans son image → si les deux diffèrent, arrêt avec un message explicite.

**`GET /status` :** les 5 vérifications s'exécutent en parallèle (`asyncio.gather` + `asyncio.to_thread` pour les appels bloquants), chacune avec son propre délai maximal → agrégation → réponse. Le résultat est mis en cache 5 s en mémoire, pour que plusieurs onglets qui interrogent en même temps ne multiplient pas les appels.

## 6. Changements d'API

### `GET /status` (nouveau)

Répond toujours **200** tant que l'API tourne : c'est un rapport, pas une sonde de santé.

```json
{
  "overall": "degraded",
  "checked_at": "2026-09-22T21:40:00Z",
  "services": {
    "database": { "status": "ok",   "detail": "révision 0001 (à jour)" },
    "redis":    { "status": "ok",   "detail": null },
    "worker":   { "status": "ok",   "detail": "occupé", "workers": 1, "current_job_id": "…" },
    "ollama":   { "status": "ok",   "detail": "0.34.1" },
    "model":    { "status": "down", "detail": "qwen3:8b absent — lancez make pull-model" }
  }
}
```

- `status` ∈ {`ok`, `degraded`, `down`}. `overall` vaut `down` si `database` ou `redis` sont `down` ; `degraded` si un autre service n'est pas `ok` ; `ok` sinon.
- `detail` : texte court destiné à l'utilisateur. Jamais de trace, d'URL interne ni de message d'exception brut.
- `ollama.detail` : version renvoyée par `GET /api/version`, si disponible.

### Endpoints inchangés

`/health` et `/ready` gardent leur contrat exact (healthcheck Docker). `GET /videos/{id}` garde son format ; seul l'ordre des listes `segments` et `summaries` devient **garanti** (croissant).

## 7. Base de données

| Révision | Contenu | Downgrade |
|---|---|---|
| `0001_baseline` | Schéma actuel à l'identique | Interdit (erreur explicite) |

Aucune autre modification de schéma en phase 0. La phase 1 ajoutera `0002_…` et les suivantes.

Table technique ajoutée par Alembic : `alembic_version (version_num varchar(32))`.

**Règles pour les migrations futures (convention d'équipe) :**
- Une migration = un changement cohérent, avec `upgrade()` **et** `downgrade()` réels.
- Nouvelle colonne sur une table existante : `nullable=True` ou `server_default` obligatoire, jamais `NOT NULL` sans valeur par défaut.
- Données (seeds, renommages) : dans une migration dédiée, idempotente (`WHERE NOT EXISTS`).
- Contraintes et index nommés explicitement.
- Pas d'import de `app.models` dans une migration : utiliser `sa.table()` / `op.*`, pour qu'une migration ancienne ne dépende pas du modèle courant.

## 8. File et traitements

- **Registre RQ** : `Worker.all(queue=Queue("video-ai"))` renvoie les workers enregistrés. La clé d'un worker expire dans Redis après `worker_ttl` sans heartbeat.
- **Délai de détection** : le `worker_ttl` par défaut de RQ (~420 s) retarderait de 7 min la détection d'un worker tué brutalement. Proposition : `Worker(..., worker_ttl=90)`. Un worker inactif renouvelle son heartbeat à chaque fin d'attente (BLPOP d'environ `worker_ttl − 15` s) ; un worker occupé le renouvelle toutes les `job_monitoring_interval` (30 s par défaut). **À vérifier sur rq 2.12** : nom exact du paramètre et comportement du heartbeat pendant un traitement, avec un test (§17).
- **Statut du worker** : `ok` si au moins un worker est enregistré. Détail « occupé » si `worker.get_state() == "busy"` (avec `current_job_id` ; l'id RQ correspond à `ProcessingJob.rq_job_id`), sinon « inactif ». `down` si aucun worker n'est enregistré.
- **Traitements** : aucun changement de logique. La suppression de `init_db()` dans `run_pipeline` ne modifie pas le comportement, puisque le schéma est garanti par `migrate`.

## 9. Fichiers et stockage

- Nouveau dossier `data/backups/`, créé par la cible `backup` et ignoré par git.
- Aucun changement dans `uploads/`, `audio/` et `exports/`.

## 10. Modèles et fournisseurs

- **Ollama** : `GET {OLLAMA_URL}/api/tags` (délai de 2 s) → `ollama` vaut `ok` si la réponse est 200. `model` vaut `ok` si `settings.llm_model` figure dans la liste. Si le nom configuré n'a pas d'étiquette, `qwen3` est comparé à `qwen3:latest`, comme Ollama le fait.
- **Whisper** : non vérifié par `/status`. Le modèle est chargé à la demande dans le worker et peut être téléchargé au premier traitement ; le vérifier obligerait à charger le modèle dans l'API. Hors périmètre, à noter comme limite connue.

## 11. CPU / GPU

Aucun impact : `migrate` utilise l'image CPU dans les deux profils. `/status` ne sollicite ni le GPU ni Whisper. L'appel `/api/tags` ne charge pas le modèle en mémoire.

## 12. Erreurs et nouvelles tentatives

| Situation | Comportement |
|---|---|
| `migrate` ne joint pas la base | 5 tentatives (1, 2, 4, 8, 16 s), puis code de sortie 1 ; `api` et `worker` ne démarrent pas (`service_completed_successfully`) |
| Base héritée non conforme à 0001 | Code de sortie 2, écarts listés dans les logs, **aucune écriture en base**. Procédure de correction documentée dans le README (sauvegarde, puis correction manuelle ou `make reset`) |
| Échec d'une migration en cours | Transaction de la migration annulée (PostgreSQL gère le DDL transactionnel) ; révision restée à la précédente ; code 1 |
| API ou worker démarrés sur une base qui n'est pas au head | `SchemaOutOfDate` au démarrage, message « Base à la révision X, attendu Y : lancez `make migrate` », arrêt du processus |
| Une vérification de `/status` dépasse son délai | Ce service vaut `down` (`detail` : « délai dépassé ») ; les autres sont renvoyés normalement ; temps de réponse borné |
| Redis indisponible | `redis` et `worker` valent `down` (le registre est dans Redis) ; `detail` de `worker` : « état inconnu (Redis indisponible) » |

## 13. Idempotence et reprise

- `python -m app.migrate` est idempotent : sur une base déjà au head, il ne fait rien et sort avec le code 0.
- Deux exécutions concurrentes sont sérialisées par le verrou consultatif PostgreSQL. La seconde trouve la base au head.
- La reprise d'une base héritée (`stamp 0001`) n'a lieu qu'une fois. Ensuite, `alembic_version` existe et l'étape 3b ne s'applique plus.
- `/status` n'écrit rien.

## 14. Observabilité

- **Logs `migrate`** (niveau INFO) : « Base neuve », « Base héritée conforme : révision 0001 posée », « Mise à jour 0001 → 0003 (1,2 s) » ou « Déjà à jour (0003) ». Niveau ERROR : liste des écarts de schéma.
- **Logs API et worker au démarrage** : « Schéma vérifié : révision 0003 ».
- **`/status`** : devient la source de diagnostic de l'utilisateur, et la base du bandeau d'accueil.
- `data/exports/<id>/metadata.json` : aucun changement en phase 0.

## 15. Sécurité

- `/status` n'expose que des informations de diagnostic locales (versions, nom du modèle, id de traitement). L'API reste liée à `127.0.0.1` (`compose.yaml`). Aucun message d'exception brut n'est renvoyé.
- `migrate` utilise les mêmes identifiants que l'API. Aucun nouveau secret.
- Sauvegardes `pg_dump` : elles contiennent transcriptions et conversations. Elles restent dans `data/backups/` en local, ignorées par git. La documentation doit signaler qu'elles sont aussi sensibles que la base.
- Le verrou consultatif utilise une clé constante propre à l'application, sans risque de collision avec d'autres usages de la base.

## 16. Performances et ressources

- `migrate` : quelques secondes au démarrage (connexion, inspection). Le conteneur s'arrête ensuite.
- Suppression d'un `create_all` par traitement (`worker.py:200`) : environ 5 requêtes d'inspection de catalogue en moins par vidéo.
- `/status` : moins de 2,5 s dans le pire cas (délai d'Ollama de 2 s + marge), environ 50 ms en temps normal. Le cache de 5 s et l'interrogation toutes les 30 s par onglet donnent une charge négligeable.
- `order_by` sur `segments` : un tri de ~6 000 lignes pour une vidéo de 6 h, négligeable. Si une mesure le justifiait plus tard, un index `(video_id, start_seconds)` pourrait être ajouté par migration.

## 17. Plan de tests

**Tests unitaires (SQLite, job CI `backend`)**

| Test | Vérifie |
|---|---|
| `test_migrations.py::test_upgrade_head_matches_models` | `upgrade head` sur une base vide, puis `compare_metadata(Base.metadata)` : **aucune différence**. Ce test empêche un modèle de diverger des migrations (un modèle modifié sans migration fait échouer la CI) |
| `test_migrations.py::test_single_head` | `ScriptDirectory.get_heads()` n'a qu'un élément (pas de branches parallèles) |
| `test_migrations.py::test_migrate_is_idempotent` | `app.migrate` lancé deux fois : code 0, révision identique |
| `test_ordering.py::test_summaries_ordered_by_created_at` | Résumés insérés dans le désordre (`created_at` forcés) → `GET /videos/{id}` les renvoie triés, et le chat utilise le plus récent |
| `test_ordering.py::test_segments_ordered_by_start` | Même principe pour les segments |
| `test_status.py` | Avec des vérifications simulées : worker absent → `worker: down`, `overall: degraded`, HTTP 200 ; modèle absent de `/api/tags` → `model: down` ; `qwen3` correspond à `qwen3:latest` ; délai d'Ollama → `down` en moins de 2,5 s ; Redis `down` → `worker` inconnu ; aucun texte d'exception dans la réponse |
| `test_schema.py` | `assert_schema_current` lève une erreur quand la révision diffère et passe quand elle correspond |
| `test_worker_entry.py` (existant) | Mis à jour : `init_db` → `assert_schema_current` ; `worker_ttl` transmis |

**Tests d'intégration (PostgreSQL, job CI renommé `backend-postgres`)**

| Test | Vérifie |
|---|---|
| `test_upgrade_head_postgres` | Comme en SQLite, sur PostgreSQL 17 |
| `test_legacy_database_is_stamped` | `create_all` (base héritée simulée) → `app.migrate` → `alembic_version = head`, données conservées, aucune différence de schéma |
| `test_legacy_drift_refused` | `create_all`, puis suppression d'une colonne → `app.migrate` sort avec le code 2 et **aucune** table `alembic_version` n'est créée |
| `test_concurrent_migrate` | Deux `app.migrate` en parallèle → les deux sortent avec le code 0, une seule mise à jour appliquée |
| `test_rq_worker_registry` | Un vrai worker RQ (Redis de service CI) avec `worker_ttl=90` apparaît dans `Worker.all`, puis disparaît après arrêt : valide l'hypothèse du §8 |

Le job `backend-postgres` ajoute un service `redis:7-alpine`. `test_concurrency.py` passe de `create_all` à `upgrade head`.

**CI Compose (job `compose`)** : `docker compose config -q` valide automatiquement le nouveau service et les `depends_on`.

**Test manuel de validation (checklist de livraison)**
1. Installation existante avec des vidéos → `make up` → `migrate` : « Base héritée conforme » → vidéos, résumés et chats intacts.
2. `docker compose stop worker` → le panneau affiche Worker « arrêté » en 90 s ou moins, et le bandeau d'accueil apparaît.
3. `docker compose exec ollama ollama rm qwen3:8b` → Modèle « absent » ; `make pull-model` → « présent ».
4. `make reset && make up` → base neuve → `migrate` : « Base neuve » → application fonctionnelle.

## 18. Plan de migration (déploiement)

1. **Sauvegarder** : `make backup` (recommandé en gras dans les notes de version).
2. `git pull && make up` : `migrate` s'exécute automatiquement.
3. Cas nominal : base héritée conforme → `stamp 0001` → rien d'autre à faire.
4. Cas d'écart (code 2) : les logs listent les écarts. Deux options documentées :
   - corriger à la main (rare : uniquement si la base a été modifiée hors de l'application), puis relancer `make migrate` ;
   - ou `make backup`, `make reset`, puis réimporter les vidéos (perte de l'historique).
5. Vérifier le panneau État du système.

## 19. Plan de retour arrière

- **Code** : revenir à l'image précédente. L'ancien code appelle `create_all`, qui est sans effet sur une base qui contient déjà toutes les tables. La table `alembic_version` est ignorée. **Retour arrière sans action sur la base.**
- **Base** : aucune modification de schéma en phase 0 (seule `alembic_version` est ajoutée) : rien à défaire. `DROP TABLE alembic_version` est possible, mais inutile.
- **Phases suivantes** : `make migrate` avec `alembic downgrade <révision>` pour chaque migration réversible. La sauvegarde de l'étape 18.1 reste le filet de sécurité ultime.

## 20. Alternatives étudiées

| Alternative | Raison du rejet |
|---|---|
| Garder `create_all` et ajouter des `ALTER TABLE … IF NOT EXISTS` dans `init_db()` | Pas de versionnement, pas de retour arrière, SQL spécifique à PostgreSQL mélangé au code, dette qui grossit à chaque phase |
| Lancer les migrations dans le `lifespan` de l'API (avec verrou) | Plus simple, sans service supplémentaire, mais : le démarrage de l'API dépend de la durée des migrations (healthcheck), le worker peut démarrer avant la fin, et une erreur de migration n'apparaît que sous la forme « api unhealthy ». Reste un repli acceptable si `service_completed_successfully` posait problème (Compose ancien) |
| Heartbeat maison du worker (clé Redis écrite toutes les N s) | Doublonne le registre RQ ; à reconsidérer seulement si le test `test_rq_worker_registry` invalide l'hypothèse du §8 |
| Intégrer Ollama et le worker à `/ready` | `/ready` pilote le healthcheck Docker et le démarrage de `web` : un modèle absent rendrait l'interface inaccessible, justement quand l'utilisateur a besoin du diagnostic |
| Retourner `latest_summary` dans l'API au lieu d'ordonner la relation | Corrige un seul endroit ; `order_by` règle aussi le chat (`main.py:363`), les segments et les futurs usages |

## 21. Séquence d'implémentation

Chaque étape correspond à une PR indépendante et testable.

| # | Étape | Contenu | Durée |
|---|---|---|---|
| 1 | **Ordre déterministe** | `order_by` dans `models.py` + `test_ordering.py` | 0,5 j |
| 2 | **Alembic + baseline** | `alembic.ini`, `migrations/`, `0001_baseline` + `baseline_metadata()`, tests SQLite de migration | 1 j |
| 3 | **Service migrate** | `app/migrate.py` (verrou, reprise, upgrade), `compose.yaml` et `compose.dev.yaml`, cibles Makefile, job CI PostgreSQL mis à jour, tests de reprise et de concurrence | 1 j |
| 4 | **Vérification de schéma** | `app/schema.py`, suppression de `init_db()` (API, worker, `run_pipeline`), `test_worker_entry` mis à jour | 0,5 j |
| 5 | **`/status` backend** | `app/status.py`, endpoint, cache 5 s, `worker_ttl=90`, `test_status.py`, `test_rq_worker_registry` | 1 j |
| 6 | **Frontend** | Panneau État du système par service, bandeau d'accueil | 0,5 j |
| 7 | **Docs et validation** | README (migrations, sauvegarde, reprise en cas d'écart), checklist manuelle du §17 | 0,5 j |

**Critère de sortie de la phase 0** : CI verte (dont `test_upgrade_head_matches_models` et `test_legacy_database_is_stamped`) ; checklist manuelle validée sur une installation existante ; la phase 1 peut créer `0002_*` sans toucher à `init_db`.

## Écarts d'implémentation (2026-09-22)

| Point de la spec | Réalisé | Raison |
|---|---|---|
| `worker_ttl=90` (§8) | `WORKER_TTL_SECONDS = 30` | Vérifié dans rq 2.12 : la clé du worker expire à `worker_ttl + 60` s (au repos) et `job_monitoring_interval + 60` = 90 s (occupé). `worker_ttl=30` donne une détection en 90 s au plus dans les deux cas ; 90 aurait donné 150 s |
| Vérifications via `asyncio.to_thread` (§5) | `ThreadPoolExecutor` dédié (6 threads) | Un contrôle bloqué continue après son délai ; il ne doit pas occuper l'exécuteur par défaut de la boucle |
| Tables absentes d'une base héritée | Créées avant le marquage `0001` | Comportement équivalent à l'ancien `create_all` ; seules les autres différences bloquent (code 2) |
| Procédure de reprise dans le README (§12, §18) | `docs/migrations.md` | Le `README.md` racine documente la boîte à outils Claude, pas l'application |
| Variable CI `RUN_POSTGRES_CONCURRENCY` | `RUN_POSTGRES_TESTS` + `RUN_REDIS_TESTS` ; job `backend-postgres` avec services PostgreSQL et Redis | Couvre les nouveaux tests d'intégration |
| Constante de file | `QUEUE_NAME` dans `app/config.py`, utilisée par l'API, le worker et `/status` | Évite trois copies de `"video-ai"` |
