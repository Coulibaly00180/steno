"""Per-service health report behind GET /status.

/ready stays the Docker healthcheck (database + Redis only): a stopped worker
or a missing model must not make the API unhealthy, otherwise the web UI
would not start and could not show this diagnostic.
"""
import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
from redis import Redis
from rq import Queue, Worker
from sqlalchemy import text

from . import ai_models
from .config import QUEUE_NAME, settings
from .db import engine
from .schema import database_revision, head_revision

# Written by the scheduler service (app.scheduler) while it runs.
SCHEDULER_HEARTBEAT_KEY = "steno:scheduler:heartbeat"
# Written by the live transcription service (app.live) while it runs.
LIVE_HEARTBEAT_KEY = "steno:live:heartbeat"
# Also written by the live service (it runs on the GPU): card memory, Whisper speed tests (n°20).
GPU_KEY = "steno:gpu"
BENCHMARK_KEY = "steno:benchmark:"
BENCHMARK_QUEUE = "steno:benchmark:queue"

logger = logging.getLogger(__name__)

CHECK_TIMEOUT_SECONDS = 2.5
CACHE_SECONDS = 5.0
OLLAMA_REQUEST_TIMEOUT_SECONDS = 1.0

_cache: tuple[float, dict] | None = None
# Dedicated threads: a hung check (e.g. a database connect without timeout)
# keeps running after its timeout and must not hold the event loop's default
# executor.
_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="status-check")


def _result(status: str, detail: str | None = None, **extra) -> dict:
    return {"status": status, "detail": detail, **extra}


def check_database() -> dict:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        current = database_revision(connection)
    expected = head_revision()
    if current != expected:
        return {"database": _result("degraded", f"révision {current or 'aucune'}, attendu {expected}")}
    return {"database": _result("ok", f"révision {current} (à jour)")}


def check_queue() -> dict:
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
    try:
        redis.ping()
    except Exception:
        logger.warning("Status check: Redis unavailable", exc_info=True)
        return {
            "redis": _result("down", "injoignable"),
            "worker": _result("down", "état inconnu (Redis indisponible)"),
        }
    try:
        # RQ drops a worker from this registry once its heartbeat key expires.
        workers = Worker.all(queue=Queue(QUEUE_NAME, connection=redis))
    except Exception:
        logger.warning("Status check: unable to read the RQ worker registry", exc_info=True)
        return {"redis": _result("ok"), "worker": _result("down", "état inconnu")}
    if not workers:
        return {"redis": _result("ok"), "worker": _result("down", "arrêté", workers=0, current_job_id=None)}
    busy = [worker for worker in workers if worker.get_state() == "busy"]
    return {
        "redis": _result("ok"),
        "worker": _result(
            "ok",
            "occupé" if busy else "inactif",
            workers=len(workers),
            current_job_id=busy[0].get_current_job_id() if busy else None,
        ),
    }


def check_scheduler() -> dict:
    """The scheduler (watched folder, backups) writes a heartbeat key that expires when it stops."""
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
    try:
        alive = redis.exists(SCHEDULER_HEARTBEAT_KEY)
    except Exception:
        logger.warning("Status check: Redis unavailable for the scheduler heartbeat", exc_info=True)
        return {"scheduler": _result("down", "état inconnu (Redis indisponible)")}
    if not alive:
        return {"scheduler": _result("down", "arrêté : dossier surveillé et sauvegardes inactifs")}
    return {"scheduler": _result("ok", "actif")}


def check_live() -> dict:
    """The live transcription service (n°11); recordings still work without it."""
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1)
    try:
        alive = redis.exists(LIVE_HEARTBEAT_KEY)
    except Exception:
        logger.warning("Status check: Redis unavailable for the live heartbeat", exc_info=True)
        return {"live": _result("down", "état inconnu (Redis indisponible)")}
    if not alive:
        return {"live": _result("down", "arrêté : pas de transcription en direct")}
    return {"live": _result("ok", f"actif · {settings.live_whisper_model}")}


def _model_tag(name: str) -> str:
    # Ollama stores untagged models as "<name>:latest".
    return name if ":" in name else f"{name}:latest"


def check_ollama() -> dict:
    base = settings.ollama_url.rstrip("/")
    with httpx.Client(timeout=OLLAMA_REQUEST_TIMEOUT_SECONDS) as client:
        try:
            tags = client.get(f"{base}/api/tags")
            tags.raise_for_status()
            models = tags.json().get("models", [])
        except (httpx.HTTPError, ValueError):
            logger.warning("Status check: Ollama unavailable", exc_info=True)
            return {
                "ollama": _result("down", "injoignable"),
                "model": _result("down", "état inconnu (Ollama injoignable)"),
                "embedding": _result("down", "état inconnu (Ollama injoignable)"),
            }
        try:
            version = client.get(f"{base}/api/version").json().get("version")
        except (httpx.HTTPError, ValueError, AttributeError):
            version = None

    installed = {_model_tag(str(model.get(key))) for model in models for key in ("name", "model") if model.get(key)}

    def presence(name: str) -> dict:
        if _model_tag(name) in installed:
            return _result("ok", f"{name} · présent")
        return _result("down", f"{name} absent — lancez le service model-pull")

    return {"ollama": _result("ok", version), "model": presence(ai_models.llm_model()), "embedding": presence(settings.embedding_model)}


async def _run(check, services: tuple[str, ...]) -> dict:
    try:
        future = asyncio.get_running_loop().run_in_executor(_executor, check)
        return await asyncio.wait_for(future, CHECK_TIMEOUT_SECONDS)
    except TimeoutError:
        return {name: _result("down", "délai dépassé") for name in services}
    except Exception:
        logger.exception("Status check %s failed", check.__name__)
        return {name: _result("down", "erreur inattendue") for name in services}


def _overall(services: dict) -> str:
    if any(services[name]["status"] == "down" for name in ("database", "redis")):
        return "down"
    if any(service["status"] != "ok" for service in services.values()):
        return "degraded"
    return "ok"


async def system_status() -> dict:
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    parts = await asyncio.gather(
        _run(check_database, ("database",)),
        _run(check_queue, ("redis", "worker")),
        _run(check_ollama, ("ollama", "model", "embedding")),
        _run(check_scheduler, ("scheduler",)),
        _run(check_live, ("live",)),
    )
    services = {name: result for part in parts for name, result in part.items()}
    ordered = {name: services[name] for name in ("database", "redis", "worker", "scheduler", "live", "ollama", "model", "embedding")}
    report = {
        "overall": _overall(ordered),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "services": ordered,
    }
    _cache = (now, report)
    return report
