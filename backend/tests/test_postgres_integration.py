"""PostgreSQL and Redis integration tests (CI job `backend-postgres`).

They reset the `public` schema of DATABASE_URL: never point them at real data.
"""
import os
import threading
import time

import pytest
from redis import Redis
from rq import Queue, Worker
from sqlalchemy import create_engine, inspect, text

from app import migrate
from app.config import QUEUE_NAME, settings
from app.db import Base
from app.schema import head_revision

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        os.getenv("RUN_POSTGRES_TESTS") != "1",
        reason="set RUN_POSTGRES_TESTS=1 to run PostgreSQL integration tests",
    ),
]


@pytest.fixture
def pg_engine():
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    yield engine
    engine.dispose()


def test_upgrade_head_postgres(pg_engine):
    assert migrate.migrate(pg_engine) == head_revision()
    from alembic.autogenerate import compare_metadata
    from alembic.runtime.migration import MigrationContext
    with pg_engine.connect() as connection:
        from app.schema import compare_type, include_object
        context = MigrationContext.configure(connection, opts={"compare_type": compare_type, "include_object": include_object})
        assert compare_metadata(context, Base.metadata) == []


def test_legacy_database_is_stamped(pg_engine):
    migrate.baseline_metadata().create_all(pg_engine)  # pre-Alembic install
    with pg_engine.begin() as connection:
        connection.execute(text(
            "INSERT INTO summary_templates (id, name, prompt, is_default, created_at) "
            "VALUES ('tpl', 'Mon template', '# A', true, now())"
        ))

    assert migrate.migrate(pg_engine) == head_revision()
    with pg_engine.connect() as connection:
        assert connection.execute(text("SELECT name FROM summary_templates WHERE is_default")).scalar_one() == "Mon template"


def test_legacy_drift_refused(pg_engine):
    migrate.baseline_metadata().create_all(pg_engine)  # pre-Alembic install
    with pg_engine.begin() as connection:
        connection.execute(text("ALTER TABLE videos DROP COLUMN translated_text"))

    with pytest.raises(migrate.SchemaDriftError):
        migrate.migrate(pg_engine)
    assert "alembic_version" not in inspect(pg_engine).get_table_names()


def test_concurrent_migrate(pg_engine):
    results, errors = [], []

    def run():
        engine = create_engine(settings.database_url)
        try:
            results.append(migrate.migrate(engine))
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)
        finally:
            engine.dispose()

    threads = [threading.Thread(target=run) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    assert results == [head_revision(), head_revision()]


@pytest.mark.skipif(os.getenv("RUN_REDIS_TESTS") != "1", reason="set RUN_REDIS_TESTS=1 to run Redis tests")
def test_rq_worker_registry_expires():
    """A worker whose heartbeat stops leaves Worker.all after worker_ttl + 60 s.

    Uses a shortened TTL so the test takes seconds, not minutes.
    """
    redis = Redis.from_url(settings.redis_url)
    queue = Queue(f"{QUEUE_NAME}-test-{time.time_ns()}", connection=redis)
    worker = Worker([queue], connection=redis, worker_ttl=1)
    worker.register_birth()
    try:
        assert worker.name in {w.name for w in Worker.all(queue=queue)}
        worker.heartbeat(timeout=1)  # simulate the last heartbeat before a crash
        time.sleep(2.5)
        assert worker.name not in {w.name for w in Worker.all(queue=queue)}
    finally:
        redis.delete(worker.key)


def test_concurrent_default_changes_leave_exactly_one_default(pg_engine, monkeypatch):
    """CA-18: two templates made default at the same time never give zero or two defaults."""
    from sqlalchemy.orm import sessionmaker

    from app import main

    migrate.migrate(pg_engine)
    monkeypatch.setattr(main, "SessionLocal", sessionmaker(bind=pg_engine, expire_on_commit=False))
    with pg_engine.connect() as connection:
        ids = connection.execute(text("SELECT id FROM summary_templates ORDER BY name")).scalars().all()[:2]

    errors = []

    def flip(template_id):
        try:
            for _ in range(10):
                main.set_default_template(template_id)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=flip, args=(template_id,)) for template_id in ids]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)

    assert errors == []
    with pg_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM summary_templates WHERE is_default")).scalar_one() == 1


def test_library_full_text_search_ignores_accents_and_matches_prefixes(pg_engine, monkeypatch):
    from datetime import datetime, timezone

    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker

    from app import main
    from app.models import Video

    migrate.migrate(pg_engine)
    session = sessionmaker(bind=pg_engine, expire_on_commit=False)
    monkeypatch.setattr(main, "SessionLocal", session)
    with session() as db:
        for video_id, name, transcript, translation in [
            ("fr", "Réunion.mp4", "[00:00:00] Le budget prévisionnel est validé\n[00:00:07] Fin", None),
            ("en", "talk.mp3", "[00:00:00] Quarterly roadmap", "[00:00:00] Feuille de route trimestrielle"),
        ]:
            db.add(Video(
                id=video_id, filename=name, original_filename=name, path=f"/x/{name}", duration_seconds=60,
                size_bytes=1, status="COMPLETED", transcript_text=transcript, translated_text=translation,
                created_at=datetime.now(timezone.utc),
            ))
        db.commit()
    client = TestClient(main.app)

    def ids(query):
        response = client.get("/videos", params={"q": query})
        assert response.status_code == 200, response.text
        return sorted(row["id"] for row in response.json())

    assert ids("previsionnel") == ["fr"]      # accent typed or not
    assert ids("prévision") == ["fr"]         # prefix
    assert ids("reunion") == ["fr"]           # file name
    assert ids("trimestrielle") == ["en"]     # translation
    assert ids("budget roadmap") == []        # every word must match
    assert ids("l'équipe & | !") == []        # operators never reach to_tsquery
    assert ids("07") == []                    # "[00:00:07]" timestamps are not indexed


def test_semantic_search_with_pgvector(pg_engine, monkeypatch):
    """Passages are stored as pgvector vectors and ranked by cosine distance in SQL."""
    from sqlalchemy.orm import sessionmaker

    from app import retrieval, worker
    from app.models import TranscriptSegment, Video
    from tests.conftest import fake_embedding

    migrate.migrate(pg_engine)
    session = sessionmaker(bind=pg_engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", session)
    with session() as db:
        for video_id, lines in [
            ("a", ["Le budget annuel est voté", "La météo de demain"]),
            ("b", ["Les vélos électriques du conseil"]),
        ]:
            db.add(Video(id=video_id, filename="x", original_filename=f"{video_id}.mp4", path="/x", duration_seconds=60,
                         size_bytes=1, status="COMPLETED", transcript_text="\n".join(lines)))
            db.flush()
            db.add_all([TranscriptSegment(video_id=video_id, start_seconds=i * 200.0, end_seconds=i * 200.0 + 5, text=line)
                        for i, line in enumerate(lines)])
        db.commit()
    worker.index_video("a")
    worker.index_video("b")

    with session() as db:
        assert retrieval.ready_video_ids(db, ["a", "b"]) == {"a", "b"}
        hits = retrieval.search(db, ["a", "b"], fake_embedding("vélos électriques"), limit=2)
        assert hits[0].video_id == "b" and hits[0].distance < hits[1].distance
        assert retrieval.search(db, ["a"], fake_embedding("budget voté"), limit=1)[0].text == "[00:00:00] Le budget annuel est voté"
        assert db.execute(text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")).scalar_one()
