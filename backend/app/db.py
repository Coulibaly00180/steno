import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def _after_fork_in_child() -> None:
    """A forked process opens its own connections.

    RQ forks a work-horse per job: without this, the child reused the parent's
    pooled connections, and two processes spoke on one PostgreSQL session
    (measured with two workers: « prepared statement "_pg3_0" already exists »,
    a failed analysis). close=False: the parent's sockets stay open for it.
    """
    engine.dispose(close=False)


os.register_at_fork(after_in_child=_after_fork_in_child)

# The schema is owned by Alembic migrations (`python -m app.migrate`, run by
# the Compose `migrate` service). The API and the worker only verify it; see
# app.schema.assert_schema_current.
