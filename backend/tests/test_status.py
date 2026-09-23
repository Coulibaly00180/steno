import time

import httpx
import pytest

from app import status

OK = {
    "database": {"database": {"status": "ok", "detail": "révision 0001 (à jour)"}},
    "queue": {
        "redis": {"status": "ok", "detail": None},
        "worker": {"status": "ok", "detail": "inactif", "workers": 1, "current_job_id": None},
    },
    "ollama": {
        "ollama": {"status": "ok", "detail": "0.34.1"},
        "model": {"status": "ok", "detail": "qwen3:8b · présent"},
        "embedding": {"status": "ok", "detail": "bge-m3 · présent"},
    },
}


@pytest.fixture(autouse=True)
def fresh_cache(monkeypatch):
    monkeypatch.setattr(status, "_cache", None)


def use_checks(monkeypatch, database=None, queue=None, ollama=None):
    monkeypatch.setattr(status, "check_database", database or (lambda: OK["database"]))
    monkeypatch.setattr(status, "check_queue", queue or (lambda: OK["queue"]))
    monkeypatch.setattr(status, "check_ollama", ollama or (lambda: OK["ollama"]))


def test_all_services_ok(client, monkeypatch):
    use_checks(monkeypatch)
    response = client.get("/status")
    assert response.status_code == 200
    body = response.json()
    assert body["overall"] == "ok"
    assert list(body["services"]) == ["database", "redis", "worker", "ollama", "model", "embedding"]


def test_stopped_worker_is_degraded_but_still_200(client, monkeypatch):
    use_checks(monkeypatch, queue=lambda: {
        "redis": {"status": "ok", "detail": None},
        "worker": {"status": "down", "detail": "arrêté", "workers": 0, "current_job_id": None},
    })
    response = client.get("/status")
    assert response.status_code == 200
    assert response.json()["overall"] == "degraded"
    assert response.json()["services"]["worker"]["detail"] == "arrêté"


def test_database_down_makes_overall_down_without_leaking_errors(client, monkeypatch):
    def broken():
        raise RuntimeError("password authentication failed for user videoai")
    use_checks(monkeypatch, database=broken)
    body = client.get("/status").json()
    assert body["overall"] == "down"
    assert body["services"]["database"] == {"status": "down", "detail": "erreur inattendue"}
    assert "password" not in str(body)


def test_slow_check_is_bounded(client, monkeypatch):
    monkeypatch.setattr(status, "CHECK_TIMEOUT_SECONDS", 0.2)
    use_checks(monkeypatch, ollama=lambda: time.sleep(1) or OK["ollama"])
    started = time.monotonic()
    body = client.get("/status").json()
    assert time.monotonic() - started < 0.9
    assert body["services"]["ollama"]["detail"] == "délai dépassé"
    assert body["services"]["model"]["status"] == "down"


def test_report_is_cached(client, monkeypatch):
    calls = []
    use_checks(monkeypatch, database=lambda: calls.append(1) or OK["database"])
    client.get("/status")
    client.get("/status")
    assert len(calls) == 1


class FakeOllama:
    def __init__(self, models=None, error=None):
        self.models, self.error = models or [], error

    def __call__(self, *args, **kwargs):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def get(self, url):
        if self.error:
            raise self.error
        request = httpx.Request("GET", url)
        if url.endswith("/api/version"):
            return httpx.Response(200, json={"version": "0.34.1"}, request=request)
        return httpx.Response(200, json={"models": [{"name": name, "model": name} for name in self.models]}, request=request)


@pytest.mark.parametrize(("configured", "installed", "expected"), [
    ("qwen3:8b", ["qwen3:8b"], "ok"),
    ("qwen3", ["qwen3:latest"], "ok"),
    ("qwen3:8b", ["llama3:8b"], "down"),
])
def test_model_presence(monkeypatch, configured, installed, expected):
    monkeypatch.setattr(status.settings, "llm_model", configured)
    monkeypatch.setattr(status.httpx, "Client", FakeOllama(installed))
    result = status.check_ollama()
    assert result["ollama"] == {"status": "ok", "detail": "0.34.1"}
    assert result["model"]["status"] == expected
    if expected == "down":
        assert "model-pull" in result["model"]["detail"]


def test_embedding_model_presence(monkeypatch):
    monkeypatch.setattr(status.settings, "embedding_model", "bge-m3")
    monkeypatch.setattr(status.httpx, "Client", FakeOllama(["qwen3:8b"]))
    assert status.check_ollama()["embedding"] == {"status": "down", "detail": "bge-m3 absent — lancez le service model-pull"}
    monkeypatch.setattr(status.httpx, "Client", FakeOllama(["qwen3:8b", "bge-m3:latest"]))
    assert status.check_ollama()["embedding"]["status"] == "ok"


def test_ollama_unreachable(monkeypatch):
    monkeypatch.setattr(status.httpx, "Client", FakeOllama(error=httpx.ConnectError("refused")))
    result = status.check_ollama()
    assert result["ollama"]["status"] == "down"
    assert result["model"]["detail"] == "état inconnu (Ollama injoignable)"


def test_redis_down_makes_worker_unknown(monkeypatch):
    class BrokenRedis:
        def ping(self):
            raise ConnectionError("refused")
    monkeypatch.setattr(status.Redis, "from_url", lambda *args, **kwargs: BrokenRedis())
    result = status.check_queue()
    assert result["redis"]["status"] == "down"
    assert result["worker"] == {"status": "down", "detail": "état inconnu (Redis indisponible)"}


def test_worker_registry(monkeypatch):
    class FakeRedis:
        def ping(self):
            return True

    class FakeWorker:
        def __init__(self, state, job=None):
            self.state, self.job = state, job

        def get_state(self):
            return self.state

        def get_current_job_id(self):
            return self.job

    monkeypatch.setattr(status.Redis, "from_url", lambda *args, **kwargs: FakeRedis())
    monkeypatch.setattr(status, "Queue", lambda *args, **kwargs: None)

    monkeypatch.setattr(status.Worker, "all", lambda **kwargs: [])
    assert status.check_queue()["worker"]["status"] == "down"

    monkeypatch.setattr(status.Worker, "all", lambda **kwargs: [FakeWorker("idle"), FakeWorker("busy", "rq-1")])
    worker = status.check_queue()["worker"]
    assert worker == {"status": "ok", "detail": "occupé", "workers": 2, "current_job_id": "rq-1"}
