from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from app import models
from app.config import settings
from app.schema import compare_type, include_object

config = context.config
# Importing app.models registers every table on the shared metadata.
target_metadata = models.Base.metadata

# app.migrate configures logging itself and passes its own connection.
if config.config_file_name is not None and "connection" not in config.attributes:
    fileConfig(config.config_file_name, disable_existing_loggers=False)


def _configure(connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=compare_type,
        include_object=include_object,
        # SQLite (tests) cannot ALTER most things in place.
        render_as_batch=connection.dialect.name == "sqlite",
        transaction_per_migration=True,
    )


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()
        return

    # Direct CLI usage (`alembic revision --autogenerate`, `alembic current`).
    engine = create_engine(settings.database_url)
    with engine.connect() as connection:
        _configure(connection)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
