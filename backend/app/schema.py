from pathlib import Path

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection, Engine

ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"


# PostgreSQL-only full-text search objects of migration 0005. They are not in
# the ORM models (SQLite, used by the unit tests, has no tsvector), so the
# model/migration comparison must ignore them.
MIGRATION_ONLY_OBJECTS = frozenset({"search_vector", "ix_videos_search_vector"})


def include_object(obj, name, type_, reflected, compare_to) -> bool:
    return not (reflected and compare_to is None and name in MIGRATION_ONLY_OBJECTS)


def compare_type(context, inspected_column, metadata_column, inspected_type, metadata_type):
    """SQLite reflects pgvector's VECTOR column as NUMERIC (type affinity): not a change."""
    from pgvector.sqlalchemy import Vector

    if isinstance(metadata_type, Vector) and context.dialect.name == "sqlite":
        return False
    return None  # Alembic's default comparison


class SchemaOutOfDate(RuntimeError):
    """The database revision differs from the migrations shipped with this code."""


def alembic_config() -> Config:
    return Config(str(ALEMBIC_INI))


def script_directory() -> ScriptDirectory:
    return ScriptDirectory.from_config(alembic_config())


def head_revision() -> str:
    head = script_directory().get_current_head()
    if head is None:
        raise RuntimeError("Aucune migration Alembic trouvée")
    return head


def database_revision(connection: Connection) -> str | None:
    return MigrationContext.configure(connection).get_current_revision()


def assert_schema_current(engine: Engine) -> str:
    """Refuse to run against a database that is not at the migration head."""
    expected = head_revision()
    with engine.connect() as connection:
        current = database_revision(connection)
    if current != expected:
        raise SchemaOutOfDate(
            f"Base à la révision {current or 'aucune'}, attendu {expected} : lancez `make migrate`"
        )
    return expected
