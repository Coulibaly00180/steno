from types import SimpleNamespace

from sqlalchemy.exc import SQLAlchemyError

from app import main


class ReadyDb:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query):
        return SimpleNamespace()


class ReadyRedis:
    def ping(self):
        return True


def test_ready_when_dependencies_are_available(client, monkeypatch):
    monkeypatch.setattr(main, "SessionLocal", lambda: ReadyDb())
    monkeypatch.setattr(main.Redis, "from_url", lambda *args, **kwargs: ReadyRedis())
    response = client.get("/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_ready_returns_503_when_database_is_unavailable(client, monkeypatch):
    class BrokenDb(ReadyDb):
        def execute(self, query):
            raise SQLAlchemyError("database unavailable")

    monkeypatch.setattr(main, "SessionLocal", lambda: BrokenDb())
    response = client.get("/ready")
    assert response.status_code == 503
    assert response.json() == {"detail": "Dépendances indisponibles"}


def test_ready_returns_503_when_redis_is_unavailable(client, monkeypatch):
    class BrokenRedis:
        def ping(self):
            raise ConnectionError("redis unavailable")

    monkeypatch.setattr(main, "SessionLocal", lambda: ReadyDb())
    monkeypatch.setattr(main.Redis, "from_url", lambda *args, **kwargs: BrokenRedis())
    response = client.get("/ready")
    assert response.status_code == 503
