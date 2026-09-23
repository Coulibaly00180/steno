"""Bring the database to the latest Alembic revision.

Run once by the Compose `migrate` service before the API and the worker
start (`python -m app.migrate`). Exit codes: 0 success, 1 failure,
2 legacy database that does not match the baseline (nothing was written).
"""
import logging
import sys
import time

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import OperationalError

from .db import engine as default_engine
from .schema import alembic_config, database_revision, script_directory

logger = logging.getLogger("app.migrate")

BASELINE_REVISION = "0001"
# Arbitrary application-specific key for pg_advisory_lock ("VIDAI").
ADVISORY_LOCK_KEY = 0x5649444149
CONNECT_DELAYS_SECONDS = (1, 2, 4, 8, 16)


class SchemaDriftError(RuntimeError):
    def __init__(self, differences: list):
        super().__init__("Le schéma existant ne correspond pas à la révision de référence")
        self.differences = differences


def baseline_metadata():
    return script_directory().get_revision(BASELINE_REVISION).module.baseline_metadata()


def _connect(engine: Engine, delays=CONNECT_DELAYS_SECONDS):
    # PostgreSQL may be "healthy" for Compose yet briefly refuse connections.
    for delay in delays:
        try:
            return engine.connect()
        except OperationalError:
            logger.warning("Base de données injoignable, nouvelle tentative dans %ss", delay)
            time.sleep(delay)
    return engine.connect()


def _adopt_legacy_database(connection, metadata) -> None:
    """Stamp a database created by create_all, after checking it matches the baseline.

    Whole missing tables are created, as the former create_all at startup would
    have done. Any other difference is refused without writing anything.
    """
    context = MigrationContext.configure(connection, opts={"compare_type": True})
    differences = compare_metadata(context, metadata)
    missing_names = {diff[1].name for diff in differences if diff[0] == "add_table"}
    blocking = [
        diff for diff in differences
        # Tables unknown to the application are left alone.
        if diff[0] not in {"add_table", "remove_table"}
        # Indexes of a missing table are created with it.
        and not (diff[0] == "add_index" and diff[1].table.name in missing_names)
    ]
    if blocking:
        raise SchemaDriftError(blocking)
    if missing_names:
        logger.info("Tables absentes créées : %s", ", ".join(sorted(missing_names)))
        # Use the metadata's own tables: autogenerate reports copies without indexes.
        metadata.create_all(connection, tables=[metadata.tables[name] for name in missing_names])
    connection.commit()


def migrate(engine: Engine = default_engine) -> str:
    """Upgrade to head and return the resulting revision."""
    config = alembic_config()
    connection = _connect(engine)
    postgres = connection.dialect.name == "postgresql"
    try:
        if postgres:
            # Serialize concurrent `migrate` runs; session-level, survives commits.
            connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": ADVISORY_LOCK_KEY})
            connection.commit()

        metadata = baseline_metadata()
        tables = set(inspect(connection).get_table_names())
        before = database_revision(connection) if "alembic_version" in tables else None
        connection.commit()

        if before is None and tables & set(metadata.tables):
            _adopt_legacy_database(connection, metadata)
            config.attributes["connection"] = connection
            command.stamp(config, BASELINE_REVISION)
            connection.commit()
            logger.info("Base héritée conforme : révision %s posée", BASELINE_REVISION)
            before = BASELINE_REVISION
        elif before is None:
            logger.info("Base neuve")

        started = time.monotonic()
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
        connection.commit()
        after = database_revision(connection)
        connection.commit()
        if after == before:
            logger.info("Déjà à jour (%s)", after)
        else:
            logger.info("Mise à jour %s → %s (%.1f s)", before or "vide", after, time.monotonic() - started)
        return after
    finally:
        if postgres:
            try:
                connection.rollback()
                connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": ADVISORY_LOCK_KEY})
                connection.commit()
            except Exception:
                logger.exception("Impossible de libérer le verrou de migration")
        connection.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s [%(name)s] %(message)s")
    try:
        migrate()
    except SchemaDriftError as exc:
        logger.error("%s. Écarts :", exc)
        for difference in exc.differences:
            logger.error("  %s", difference)
        logger.error("Aucune modification n'a été faite. Voir docs/migrations.md.")
        return 2
    except Exception:
        logger.exception("Échec de la migration")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
