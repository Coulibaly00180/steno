from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

# The schema is owned by Alembic migrations (`python -m app.migrate`, run by
# the Compose `migrate` service). The API and the worker only verify it; see
# app.schema.assert_schema_current.
