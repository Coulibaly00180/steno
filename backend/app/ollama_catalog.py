"""The catalog of ollama.com: which models exist, in which variants, before downloading one.

Ollama has no documented API for its library: the pages ollama.com/library and
ollama.com/library/<model>/tags are read (checked 2026-09-25: one <li> per
model with its description, capabilities, sizes, pulls; one row per variant
with its size and context window). Models come and go often: the catalog is
fetched again every 6 hours, and a model typed by hand is checked against
Ollama's registry before any download (a missing model answers 404).

Nothing is sent to ollama.com but these public page requests; without network,
the last catalog fetched stays shown, marked as such.
"""
import html
import json
import logging
import re
import time

import httpx

logger = logging.getLogger(__name__)

LIBRARY_URL = "https://ollama.com/library"
TAGS_URL = "https://ollama.com/library/{name}/tags"
MANIFEST_URL = "https://registry.ollama.ai/v2/{repository}/manifests/{tag}"
CACHE_KEY = "steno:ollama-catalog"
TAGS_KEY = "steno:ollama-tags:"
CACHE_SECONDS = 6 * 3600
TIMEOUT_SECONDS = 15
# library/qwen3, qwen3:8b, someone/model:q4_K_M
NAME = re.compile(r"^(?:[a-z0-9][a-z0-9._-]{0,63}/)?[a-z0-9][a-z0-9._-]{0,80}(?::[A-Za-z0-9][A-Za-z0-9._-]{0,80})?$")
# Model sizes are lowercase (« 8b », « 270m »); the pull count is not (« 40.7M »).
_SIZE = re.compile(r"^(?:\d+(?:\.\d+)?[mbkt]|\d+x\d+(?:\.\d+)?b|e\d+b)$")
_ENTRY = re.compile(r'<li[^>]*>\s*<a href="/library/([a-z0-9._-]+)"(.*?)</li>', re.S)
_SPAN = re.compile(r"<span[^>]*>([^<]{1,40})</span>")
_TAG_ROW = re.compile(r'<a href="/library/([a-z0-9._-]+:[A-Za-z0-9._-]+)" class="md:hidden(.*?)</a>', re.S)
_BYTES = re.compile(r"([\d.]+)\s*([KMGT]?B)\b")
_UNITS = {"B": 1, "KB": 1e3, "MB": 1e6, "GB": 1e9, "TB": 1e12}


class CatalogError(RuntimeError):
    """ollama.com could not be read (no network, page changed…), with a message for the user."""


def _text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", fragment)).strip()


def parse_count(value: str) -> int | None:
    """« 37.9M » → 37 900 000."""
    match = re.fullmatch(r"([\d.]+)\s*([KMB]?)", value.strip())
    if not match:
        return None
    return int(float(match.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[match.group(2)])


def parse_library(page: str) -> list[dict]:
    models = []
    for name, block in _ENTRY.findall(page):
        description = re.search(r'<p class="max-w-lg[^"]*"[^>]*>(.*?)</p>', block, re.S)
        # The badges come before the line of pulls, tags and update.
        badges = block.split('<p class="my-4', 1)[0]
        spans = [html.unescape(span).strip() for span in _SPAN.findall(badges)]
        sizes = [span for span in spans if _SIZE.match(span)]
        capabilities = [span for span in spans if span in {"tools", "thinking", "vision", "embedding", "cloud", "audio"}]
        pulls = re.search(r"<span\s*>([\d.]+[KMB]?)</span>\s*<span[^>]*>&nbsp;Pulls", block)
        updated = re.search(r'title="([^"]+)">\s*(?:<svg.*?</svg>)?\s*<span class="hidden sm:flex">Updated', block, re.S)
        models.append({
            "name": name,
            "description": _text(description.group(1)) if description else "",
            "capabilities": capabilities,
            "sizes": sizes,
            "pulls": parse_count(pulls.group(1)) if pulls else None,
            "updated": updated.group(1) if updated else None,
        })
    return models


def parse_tags(page: str) -> list[dict]:
    tags, seen = [], set()
    for tag, block in _TAG_ROW.findall(page):
        if tag in seen:
            continue
        seen.add(tag)
        text = " ".join(_text(block).split())
        size = _BYTES.search(text.split("•", 2)[1] if text.count("•") >= 2 else text)
        context = re.search(r"([\d.]+[KM]?) context window", text)
        inputs = re.search(r"([A-Za-z, ]+) input", text)
        tags.append({
            "name": tag,
            "size_bytes": int(float(size.group(1)) * _UNITS[size.group(2)]) if size else None,
            "context": context.group(1) if context else None,
            "input": inputs.group(1).strip() if inputs else None,
            "latest": "latest" in text.split("•", 1)[0],
        })
    return tags


def _cached(redis, key: str) -> dict | None:
    try:
        raw = redis.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _store(redis, key: str, value: dict) -> None:
    try:
        # Kept a week: the last catalog fetched stays shown without network.
        redis.set(key, json.dumps(value), ex=7 * 24 * 3600)
    except Exception:
        logger.warning("Unable to cache the Ollama catalog", exc_info=True)


def _fetch(url: str) -> str:
    try:
        response = httpx.get(url, timeout=TIMEOUT_SECONDS, follow_redirects=True, headers={"User-Agent": "Steno"})
    except httpx.HTTPError as exc:
        raise CatalogError("ollama.com ne répond pas : vérifiez l'accès à Internet") from exc
    if response.status_code == 404:
        raise CatalogError("Modèle introuvable sur ollama.com")
    if response.status_code != 200:
        raise CatalogError(f"ollama.com a répondu {response.status_code}")
    return response.text


def library(redis, *, refresh: bool = False, now: float | None = None) -> dict:
    """{models, fetched_at, stale, error}: from the cache when younger than 6 hours."""
    now = now or time.time()
    cached = _cached(redis, CACHE_KEY)
    if cached and not refresh and now - cached["fetched_at"] < CACHE_SECONDS:
        return cached | {"stale": False, "error": None}
    try:
        models = parse_library(_fetch(LIBRARY_URL))
        if not models:
            raise CatalogError("La page du catalogue d'Ollama a changé : aucun modèle reconnu")
    except CatalogError as exc:
        if cached:
            return cached | {"stale": True, "error": str(exc)}
        return {"models": [], "fetched_at": None, "stale": True, "error": str(exc)}
    result = {"models": models, "fetched_at": now}
    _store(redis, CACHE_KEY, result)
    return result | {"stale": False, "error": None}


def model_tags(redis, name: str, *, now: float | None = None) -> dict:
    """The variants of one model (name:tag, size, context)."""
    if not NAME.match(name) or ":" in name:
        raise CatalogError("Nom de modèle invalide")
    now = now or time.time()
    key = TAGS_KEY + name
    cached = _cached(redis, key)
    if cached and now - cached["fetched_at"] < CACHE_SECONDS:
        return cached
    tags = parse_tags(_fetch(TAGS_URL.format(name=name)))
    result = {"name": name, "tags": tags, "fetched_at": now}
    _store(redis, key, result)
    return result


def exists(name: str) -> bool | None:
    """Is `name` (model or model:tag) in Ollama's registry? None when the registry cannot be reached."""
    if not NAME.match(name):
        return False
    repository, _, tag = name.partition(":")
    if "/" not in repository:
        repository = f"library/{repository}"
    try:
        response = httpx.head(
            MANIFEST_URL.format(repository=repository, tag=tag or "latest"), timeout=TIMEOUT_SECONDS, follow_redirects=True,
            headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"},
        )
    except httpx.HTTPError:
        return None
    if response.status_code == 200:
        return True
    if response.status_code == 404:
        return False
    return None
