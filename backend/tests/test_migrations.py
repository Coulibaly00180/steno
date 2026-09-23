"""Alembic migrations on SQLite (fast). PostgreSQL variants: test_postgres_migrations.py."""
import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from app import migrate
from app.db import Base
from app.schema import SchemaOutOfDate, alembic_config, assert_schema_current, compare_type, head_revision, include_object, script_directory


@pytest.fixture
def sqlite_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'migrations.db'}")
    yield engine
    engine.dispose()


def schema_differences(engine, metadata=Base.metadata):
    with engine.connect() as connection:
        return compare_metadata(MigrationContext.configure(connection, opts={"compare_type": compare_type, "include_object": include_object}), metadata)


def test_upgrade_head_matches_models(sqlite_engine):
    """A model changed without its migration fails here."""
    assert migrate.migrate(sqlite_engine) == head_revision()
    assert schema_differences(sqlite_engine) == []


def test_single_head():
    assert len(script_directory().get_heads()) == 1


def test_migrate_is_idempotent(sqlite_engine):
    first = migrate.migrate(sqlite_engine)
    assert migrate.migrate(sqlite_engine) == first


def test_baseline_metadata_matches_revision_0001(sqlite_engine):
    config = alembic_config()
    with sqlite_engine.connect() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, migrate.BASELINE_REVISION)
        connection.commit()
    assert schema_differences(sqlite_engine, migrate.baseline_metadata()) == []


def test_downgrade_to_baseline_and_back(sqlite_engine):
    migrate.migrate(sqlite_engine)
    config = alembic_config()
    with sqlite_engine.connect() as connection:
        config.attributes["connection"] = connection
        command.downgrade(config, migrate.BASELINE_REVISION)
        connection.commit()
    assert schema_differences(sqlite_engine, migrate.baseline_metadata()) == []
    migrate.migrate(sqlite_engine)
    assert schema_differences(sqlite_engine) == []


def test_legacy_database_is_stamped_and_keeps_its_data(sqlite_engine):
    migrate.baseline_metadata().create_all(sqlite_engine)  # pre-Alembic install
    with sqlite_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO summary_templates (id, name, prompt, is_default, created_at) "
            "VALUES ('tpl', 'Mon template', '# A', 1, '2026-01-01')"
        ))

    assert migrate.migrate(sqlite_engine) == head_revision()
    with sqlite_engine.connect() as connection:
        rows = dict(connection.execute(text("SELECT name, is_default FROM summary_templates")).all())
    # The user's default stays the default; starter templates are added beside it.
    assert rows == {
        "Mon template": 1,
        "Compte-rendu de réunion": 0,
        "Cours / formation": 0,
        "Podcast / interview": 0,
        "Présentation / démo": 0,
    }


def test_template_data_migration(sqlite_engine):
    migrate.baseline_metadata().create_all(sqlite_engine)  # pre-Alembic install
    with sqlite_engine.begin() as connection:
        # The historical seed, plus a second default that the unique index forbids.
        connection.execute(text(
            "INSERT INTO summary_templates (id, name, prompt, is_default, created_at) VALUES "
            "('seed', 'Compte-rendu standard', '# A', 1, '2026-01-01'),"
            "('dup', 'Autre défaut', '# B', 1, '2026-02-01'),"
            "('mine', 'Podcast / interview', '# Mon podcast', 0, '2026-03-01')"
        ))

    migrate.migrate(sqlite_engine)

    with sqlite_engine.connect() as connection:
        rows = {name: (tid, default, prompt) for tid, name, default, prompt in connection.execute(
            text("SELECT id, name, is_default, prompt FROM summary_templates"))}
    assert rows["Compte-rendu de réunion"][:2] == ("seed", 1)  # renamed, still the default
    assert rows["Autre défaut"][1] == 0  # only the oldest default is kept
    assert rows["Podcast / interview"] == ("mine", 0, "# Mon podcast")  # user template untouched
    assert {"Cours / formation", "Présentation / démo"} <= set(rows)
    assert "Compte-rendu standard" not in rows


def test_fresh_install_has_starter_templates_with_meeting_default(sqlite_engine):
    migrate.migrate(sqlite_engine)
    with sqlite_engine.connect() as connection:
        defaults = connection.execute(text("SELECT name FROM summary_templates WHERE is_default")).scalars().all()
        count = connection.execute(text("SELECT count(*) FROM summary_templates")).scalar_one()
    assert defaults == ["Compte-rendu de réunion"]
    assert count == 4


def test_legacy_database_missing_a_whole_table_gets_it(sqlite_engine):
    """An install older than a table: the former create_all would have added it."""
    migrate.baseline_metadata().create_all(sqlite_engine)  # pre-Alembic install
    with sqlite_engine.begin() as connection:
        connection.execute(text("DROP TABLE video_chat_messages"))

    migrate.migrate(sqlite_engine)

    assert "video_chat_messages" in inspect(sqlite_engine).get_table_names()
    assert schema_differences(sqlite_engine) == []


def test_legacy_drift_is_refused_without_writing(sqlite_engine):
    migrate.baseline_metadata().create_all(sqlite_engine)  # pre-Alembic install
    with sqlite_engine.begin() as connection:
        connection.execute(text("ALTER TABLE videos DROP COLUMN translated_text"))

    with pytest.raises(migrate.SchemaDriftError) as error:
        migrate.migrate(sqlite_engine)

    assert any("translated_text" in str(difference) for difference in error.value.differences)
    assert "alembic_version" not in inspect(sqlite_engine).get_table_names()


def test_main_exit_codes(monkeypatch):
    monkeypatch.setattr(migrate, "migrate", lambda: "0001")
    assert migrate.main() == 0

    def drift():
        raise migrate.SchemaDriftError([("add_column", None, "videos", "x")])
    monkeypatch.setattr(migrate, "migrate", drift)
    assert migrate.main() == 2

    def failure():
        raise RuntimeError("boom")
    monkeypatch.setattr(migrate, "migrate", failure)
    assert migrate.main() == 1


def test_assert_schema_current(sqlite_engine):
    with pytest.raises(SchemaOutOfDate, match="make migrate"):
        assert_schema_current(sqlite_engine)
    migrate.migrate(sqlite_engine)
    assert assert_schema_current(sqlite_engine) == head_revision()
