import hashlib
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app import main, worker
from app.db import Base

FAKE_EMBEDDING_DIMENSIONS = 64


def fake_embedding(text: str) -> list[float]:
    """Bag of words hashed into 64 dimensions: texts sharing words are close."""
    vector = [0.0] * FAKE_EMBEDDING_DIMENSIONS
    for word in re.findall(r"\w+", text.casefold()):
        vector[int(hashlib.md5(word.encode()).hexdigest(), 16) % FAKE_EMBEDDING_DIMENSIONS] += 1.0
    return vector if any(vector) else [1.0] + [0.0] * (FAKE_EMBEDDING_DIMENSIONS - 1)


@pytest.fixture(autouse=True)
def offline_embeddings(monkeypatch):
    """Unit tests never call Ollama: without it, each call waited for a network timeout."""
    calls = []

    def embed(texts, on_batch=None):
        calls.append(list(texts))
        if on_batch and texts:
            on_batch(len(texts), len(texts))
        return [fake_embedding(text) for text in texts]

    from app import verification

    monkeypatch.setattr(worker, "embed_texts", embed)
    monkeypatch.setattr(main, "embed_texts", embed)
    monkeypatch.setattr(verification, "embed_texts", embed)
    return calls


@pytest.fixture(autouse=True)
def no_entity_jobs(monkeypatch):
    """Entity extraction (n°16) is queued after many actions: tests record it instead of using Redis."""
    queued = []
    monkeypatch.setattr(worker, "enqueue_entities_job", queued.append)
    monkeypatch.setattr(main, "enqueue_entities_job", queued.append)
    return queued


@pytest.fixture(autouse=True)
def no_action_llm(monkeypatch):
    """Actions and decisions (n°5) are asked to the LLM after each summary: none in the tests, unless one says so."""
    from app import actions

    monkeypatch.setattr(actions, "extract", lambda video, summary: [])


@pytest.fixture(autouse=True)
def open_access(monkeypatch):
    """No password (n°15) unless a test sets one: the access check never reads the database."""
    from app import auth
    from app.schemas import AccessSettings

    monkeypatch.setattr(auth, "cached", lambda: AccessSettings())
    monkeypatch.setattr(auth, "load", lambda: AccessSettings())


@pytest.fixture
def upload_environment(monkeypatch, tmp_path):
    """Use an isolated SQLite database and filesystem-backed upload directory."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()

    monkeypatch.setattr(main, "SessionLocal", test_session)
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)
    monkeypatch.setattr(main.settings, "max_upload_bytes", 16)
    monkeypatch.setattr(main, "ffprobe_duration", lambda _, **__: 1.0)
    return test_session, uploads_dir


@pytest.fixture
def client():
    """Create a client without running the application's external-service startup hook."""
    return TestClient(main.app)
