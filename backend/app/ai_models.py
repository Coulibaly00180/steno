"""The models in use (n°20): chosen in the interface, else those of the environment.

The LLM is read at each call (a few seconds of cache), the Whisper model when
a job starts: a change applies to the next analyses, never to one half done
by another model... except a summary already running, whose next LLM calls
use the new model.
"""
import logging
import time

from . import app_settings
from .config import settings
from .db import SessionLocal
from .schemas import ModelSettings

logger = logging.getLogger(__name__)

CACHE_SECONDS = 5.0
# Whisper sizes faster-whisper downloads by name; VRAM and speed are indicative (float16 GPU, int8 CPU).
WHISPER_CHOICES = [
    {"name": "tiny", "size": "75 Mo", "note": "Très rapide, peu fiable : essais seulement."},
    {"name": "base", "size": "145 Mo", "note": "Rapide, erreurs fréquentes sur les noms."},
    {"name": "small", "size": "480 Mo", "note": "Bon compromis sans GPU (réglage par défaut en CPU)."},
    {"name": "medium", "size": "1,5 Go", "note": "Plus juste que small, deux à trois fois plus lent."},
    {"name": "large-v3", "size": "3 Go", "note": "Le plus précis, lent sans GPU."},
    {"name": "large-v3-turbo", "size": "1,6 Go", "note": "Presque aussi précis que large-v3, bien plus rapide (réglage par défaut sur GPU)."},
    {"name": "distil-large-v3", "size": "1,5 Go", "note": "Rapide, anglais surtout."},
]
WHISPER_NAMES = {choice["name"] for choice in WHISPER_CHOICES}
# Ollama models known to work with Sténo, with the GPU memory they need at 32k context (indicative).
LLM_SUGGESTIONS = [
    {"name": "qwen3:4b", "vram": "~5 Go", "note": "Léger : petites cartes ou CPU."},
    {"name": "qwen3:8b", "vram": "~9 Go", "note": "Réglage par défaut, calibré sur le corpus de référence."},
    {"name": "qwen3:14b", "vram": "~13 Go", "note": "Résumés plus fins, deux fois plus lent."},
    {"name": "gemma3:12b", "vram": "~12 Go", "note": "Bon en français, autre famille pour comparer."},
    {"name": "mistral-small3.2:24b", "vram": "~20 Go", "note": "Au-delà de 16 Go : déborde en mémoire système."},
]

_cache: tuple[float, ModelSettings] | None = None


def chosen() -> ModelSettings:
    """The stored choice (fields None: environment defaults), cached a few seconds."""
    global _cache
    now = time.monotonic()
    if _cache is not None and now - _cache[0] < CACHE_SECONDS:
        return _cache[1]
    try:
        with SessionLocal() as db:
            value = app_settings.load(db, app_settings.MODELS, ModelSettings)
    except Exception:
        # The database is down: the environment's models still work.
        logger.warning("Unable to read the model choice; environment defaults used", exc_info=True)
        value = ModelSettings()
    _cache = (now, value)
    return value


def forget() -> None:
    global _cache
    _cache = None


def llm_model() -> str:
    return chosen().llm_model or settings.llm_model


def whisper_model() -> str:
    return chosen().whisper_model or settings.whisper_model
