"""The ollama.com catalog: parsed from its real pages (trimmed), cached, and the check before a download."""
from pathlib import Path

import httpx
import pytest

from app import main, ollama_catalog

HERE = Path(__file__).parent
LIBRARY = (HERE / "ollama_library.html").read_text(encoding="utf-8")
TAGS = (HERE / "ollama_qwen3_tags.html").read_text(encoding="utf-8")
# The real check (conftest replaces it for the other tests).
REAL_EXISTS = ollama_catalog.exists


class FakeRedis:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None):
        self.values[key] = value


def test_the_library_page_gives_each_model_its_sizes_and_capabilities():
    models = {model["name"]: model for model in ollama_catalog.parse_library(LIBRARY)}
    assert set(models) == {"qwen3", "gemma3", "bge-m3", "kimi-k2.6", "openhermes"}
    qwen = models["qwen3"]
    assert qwen["sizes"] == ["0.6b", "1.7b", "4b", "8b", "14b", "30b", "32b", "235b"]
    assert qwen["capabilities"] == ["tools", "thinking"] and qwen["pulls"] == 37_900_000
    assert qwen["description"].startswith("Qwen3 is the latest generation")
    # The pull count (« 40.7M ») is not taken for a size.
    assert models["gemma3"]["sizes"] == ["270m", "1b", "4b", "12b", "27b"]
    assert models["bge-m3"]["capabilities"] == ["embedding"]


def test_the_tags_page_gives_the_variants_and_their_size():
    tags = ollama_catalog.parse_tags(TAGS)
    assert [tag["name"] for tag in tags] == ["qwen3:latest", "qwen3:0.6b", "qwen3:1.7b", "qwen3:4b"]
    assert tags[0] == {"name": "qwen3:latest", "size_bytes": 5_200_000_000, "context": "40K", "input": "Text", "latest": True}
    assert tags[3]["context"] == "256K"


def test_the_catalog_is_kept_six_hours_and_shown_stale_without_network(monkeypatch):
    redis, calls = FakeRedis(), []

    def fetch(url):
        calls.append(url)
        return LIBRARY

    monkeypatch.setattr(ollama_catalog, "_fetch", fetch)
    first = ollama_catalog.library(redis, now=1000.0)
    assert len(first["models"]) == 5 and not first["stale"] and first["error"] is None
    ollama_catalog.library(redis, now=1000.0 + 3600)
    assert len(calls) == 1
    # Past 6 hours and ollama.com unreachable: the last catalog, marked as old.

    def offline(url):
        raise ollama_catalog.CatalogError("ollama.com ne répond pas : vérifiez l'accès à Internet")

    monkeypatch.setattr(ollama_catalog, "_fetch", offline)
    stale = ollama_catalog.library(redis, now=1000.0 + 7 * 3600)
    assert stale["stale"] and "ne répond pas" in stale["error"] and len(stale["models"]) == 5
    # Never fetched: an empty catalog and the reason.
    empty = ollama_catalog.library(FakeRedis(), now=1.0)
    assert empty["models"] == [] and empty["error"]


def test_a_changed_page_is_reported_not_shown_empty(monkeypatch):
    monkeypatch.setattr(ollama_catalog, "_fetch", lambda url: "<html>nouvelle mise en page</html>")
    result = ollama_catalog.library(FakeRedis(), now=1.0)
    assert result["models"] == [] and "a changé" in result["error"]


@pytest.mark.parametrize("status, expected", [(200, True), (404, False), (503, None)])
def test_a_model_is_checked_in_the_registry(monkeypatch, status, expected):
    asked = []

    def head(url, **kwargs):
        asked.append(url)
        return httpx.Response(status)

    monkeypatch.setattr(ollama_catalog.httpx, "head", head)
    assert REAL_EXISTS("qwen3:8b") is expected
    assert asked == ["https://registry.ollama.ai/v2/library/qwen3/manifests/8b"]
    REAL_EXISTS("someone/model")
    assert asked[-1] == "https://registry.ollama.ai/v2/someone/model/manifests/latest"
    assert REAL_EXISTS("../../etc") is False


def test_the_catalog_routes(client, monkeypatch):
    redis = FakeRedis()
    monkeypatch.setattr(main, "_catalog_redis", lambda: redis)
    monkeypatch.setattr(ollama_catalog, "_fetch", lambda url: TAGS if url.endswith("/tags") else LIBRARY)
    monkeypatch.setattr(main, "_installed_models", lambda: [{"name": "qwen3:8b"}, {"name": "qwen3:4b"}])
    monkeypatch.setattr(main, "_redis_json", lambda key: {"memory_total_mb": 16303})
    catalog = client.get("/models/catalog").json()
    names = [model["name"] for model in catalog["models"]]
    # kimi-k2.6 runs only in Ollama's cloud: of no use to Sténo.
    assert "kimi-k2.6" not in names and "qwen3" in names
    assert next(model for model in catalog["models"] if model["name"] == "qwen3")["installed"]
    tags = {tag["name"]: tag for tag in client.get("/models/catalog/qwen3").json()["tags"]}
    assert tags["qwen3:4b"]["installed"] and not tags["qwen3:0.6b"]["installed"]
    # 5.2 GB file: ~11 GB with the context, in a 16 GB card.
    assert tags["qwen3:latest"]["fits"] is True and 10e9 < tags["qwen3:latest"]["vram_bytes"] < 12e9
    assert client.get("/models/catalog/..%2F..").status_code in (404, 503)


def test_a_model_missing_from_the_registry_is_not_downloaded(client, monkeypatch):
    monkeypatch.setattr(ollama_catalog, "exists", lambda name: False)
    response = client.post("/models/llm/pull", json={"name": "qwen3:999b"})
    assert response.status_code == 422 and "n'existe pas" in response.json()["detail"]
