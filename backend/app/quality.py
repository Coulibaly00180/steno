"""Quality runs (n°4): replay the reference corpus and compare the scores with the previous run.

The corpus (data/corpus, see its README) holds freely licensed recordings and,
in references.json, the subjects each summary must cover, taken from the
official descriptions only. A run transcribes each recording (cached per
Whisper model: only a Whisper change transcribes again), summarises it with
exactly the analysis code (`worker.compose_summary`), and scores it:
coverage of the expected subjects, length against the word budget, expected
chapter starts found, language detected.

A run starts by itself when the prompts or the models change (fingerprint
below), on the secondary queue: it never delays a user's analysis.
"""
import hashlib
import inspect
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from redis import Redis
from rq import Queue
from sqlalchemy import select

from . import ai_models, analysis_options, app_settings, llm
from .config import INDEX_QUEUE_NAME, settings
from .db import SessionLocal
from .models import QualityRun, SummaryTemplate
from .schemas import QualitySettings

logger = logging.getLogger(__name__)

SCOPES = {
    # About 5 minutes on an RTX 5080 once transcribed: a meeting, a podcast, a short clip.
    "quick": ("clip-fr", "reunion-fr", "podcast-en"),
    # Adds 5 hours of course and conferences: about 25 minutes.
    "full": ("clip-fr", "reunion-fr", "podcast-en", "cours-fr", "conference-en"),
}
SCOPE_LABELS = {"quick": "rapide", "full": "complet"}
# The template a user would pick for each kind of content (as data/corpus/evaluate.py).
TEMPLATES = {
    "reunion-fr": "Compte-rendu de réunion",
    "cours-fr": "Cours / formation",
    "podcast-en": "Podcast / interview",
    "conference-en": "Présentation / démo",
    "clip-fr": "Cours / formation",
}
ACTIVE = ("QUEUED", "RUNNING")
CHAPTER_TOLERANCE_SECONDS = 120
# Length outside this share of the word budget is reported (the prompts aim at 0.8 to 1.2).
LENGTH_RANGE = (0.5, 1.5)


class RunCancelled(RuntimeError):
    pass


def now() -> datetime:
    return datetime.now(timezone.utc)


def corpus_dir() -> Path:
    return settings.data_dir / "corpus"


def load_corpus() -> tuple[dict, dict] | None:
    """(sources, references), or None when the corpus is not on this machine."""
    try:
        sources = json.loads((corpus_dir() / "sources.json").read_text(encoding="utf-8"))
        references = json.loads((corpus_dir() / "references.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return sources, references


def corpus_items() -> list[dict]:
    """The corpus recordings, and whether each is present (the media are not versioned)."""
    corpus = load_corpus()
    if corpus is None:
        return []
    sources, references = corpus
    return [
        {
            "key": key,
            "title": source.get("title", key),
            "duration_seconds": source.get("duration_seconds"),
            "language": references.get(key, {}).get("language"),
            "topics": len(references.get(key, {}).get("topics", {})),
            "present": (corpus_dir() / source.get("file", "")).is_file(),
            "scopes": [scope for scope, keys in SCOPES.items() if key in keys],
        }
        for key, source in sources.items()
    ]


def corpus_available() -> bool:
    return any(item["present"] for item in corpus_items() if "quick" in item["scopes"])


def _template_prompts(db) -> dict[str, str]:
    rows = db.scalars(select(SummaryTemplate).where(SummaryTemplate.name.in_(set(TEMPLATES.values()))))
    return {row.name: row.prompt for row in rows}


def fingerprint(db) -> tuple[str, str]:
    """(fingerprint of prompts + models, short prompt version). A change starts an automatic run."""
    from .worker import DEFAULT_TEMPLATE

    prompts = "\n".join([
        inspect.getsource(llm), inspect.getsource(analysis_options), DEFAULT_TEMPLATE,
        json.dumps(_template_prompts(db), sort_keys=True, ensure_ascii=False),
    ])
    prompt_hash = hashlib.sha256(prompts.encode("utf-8")).hexdigest()
    from .transcription import TRANSCRIPTION_VERSION

    full = "|".join([prompt_hash, ai_models.llm_model(), ai_models.whisper_model(), str(settings.llm_num_ctx),
                     f"transcription{TRANSCRIPTION_VERSION}"])
    return hashlib.sha256(full.encode("utf-8")).hexdigest(), prompt_hash[:8]


def score_item(summary: str, duration: float, chapters: list[tuple[float, str]], reference: dict, language: str | None,
               transcript: str | None = None) -> dict:
    """Scores of one summary against its reference (the method of data/corpus/score.py).

    With the transcript, only the subjects actually said count: the references
    come from the official descriptions, which say more than the recording
    (clip-fr: 3 of its 4 subjects are never said, so its summary could not
    score over 25 %). The others are listed as `unsaid`.
    """
    topics = reference.get("topics", {})
    unsaid: list[str] = []
    if transcript is not None:
        heard = transcript.lower()
        unsaid = [name for name, pattern in topics.items() if not re.search(pattern, heard)]
        topics = {name: pattern for name, pattern in topics.items() if name not in unsaid}
    lowered = summary.lower()
    missing = [name for name, pattern in topics.items() if not re.search(pattern, lowered)]
    words = len(re.findall(r"\w+", summary))
    budget = analysis_options.word_budget(duration, "standard")
    expected = reference.get("chapter_starts")
    hits = None
    if expected:
        hits = sum(any(abs(start - wanted) <= CHAPTER_TOLERANCE_SECONDS for start, _ in chapters) for wanted in expected)
    return {
        "covered": len(topics) - len(missing),
        "topics": len(topics),
        "coverage": (len(topics) - len(missing)) / len(topics) if topics else None,
        "missing": missing,
        "unsaid": unsaid,
        "words": words,
        "budget": budget,
        "length_ratio": round(words / budget, 3) if budget else None,
        "chapters": len(chapters),
        "chapter_hits": hits,
        "chapter_expected": len(expected) if expected else None,
        "language": language,
        "language_ok": language == reference.get("language") if reference.get("language") else None,
    }


def overall_score(results: dict) -> float | None:
    """Mean coverage of the expected subjects, in percent."""
    values = [item["coverage"] for item in results.values() if isinstance(item, dict) and item.get("coverage") is not None]
    return round(100 * sum(values) / len(values), 1) if values else None


def compare(results: dict, previous: dict | None) -> list[dict]:
    """What got worse or better since the previous run: one message per item and measure."""
    changes = []
    for key, item in results.items():
        before = (previous or {}).get(key)
        if not isinstance(item, dict) or item.get("error"):
            continue
        if not isinstance(before, dict) or before.get("error"):
            continue
        if item.get("topics") and item["covered"] != before.get("covered"):
            changes.append({
                "key": key, "measure": "coverage", "worse": item["covered"] < (before.get("covered") or 0),
                "text": f"couverture {before.get('covered')}/{before.get('topics')} → {item['covered']}/{item['topics']}",
            })
        ratio, old_ratio = item.get("length_ratio"), before.get("length_ratio")
        if ratio is not None and old_ratio is not None:
            inside = LENGTH_RANGE[0] <= ratio <= LENGTH_RANGE[1]
            was_inside = LENGTH_RANGE[0] <= old_ratio <= LENGTH_RANGE[1]
            if inside != was_inside:
                changes.append({
                    "key": key, "measure": "length", "worse": not inside,
                    "text": f"longueur {old_ratio:.0%} → {ratio:.0%} du budget",
                })
        if item.get("chapter_expected") and item.get("chapter_hits") != before.get("chapter_hits"):
            changes.append({
                "key": key, "measure": "chapters", "worse": (item["chapter_hits"] or 0) < (before.get("chapter_hits") or 0),
                "text": f"débuts de chapitres {before.get('chapter_hits')}/{item['chapter_expected']} → {item['chapter_hits']}/{item['chapter_expected']}",
            })
        if item.get("language_ok") is False and before.get("language_ok"):
            changes.append({"key": key, "measure": "language", "worse": True, "text": f"langue détectée : {item.get('language')}"})
    return changes


# --- runs -----------------------------------------------------------------------------------------


def _update(run_id: str, **fields) -> None:
    with SessionLocal() as db:
        run = db.get(QualityRun, run_id)
        if run is None:
            raise RunCancelled(run_id)
        if run.status == "CANCELLED":
            raise RunCancelled(run_id)
        for name, value in fields.items():
            setattr(run, name, value)
        db.commit()


def _transcript_cache(key: str, whisper: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9.-]+", "_", whisper)
    from .transcription import TRANSCRIPTION_VERSION

    return corpus_dir() / ".cache" / f"{key}--{safe}--beam{settings.whisper_beam_size}--v{TRANSCRIPTION_VERSION}.json"


def transcript_for(key: str, media: Path, on_progress=None, on_wait=None, check=None) -> dict:
    """{rows, language, seconds}: from the cache, else Whisper (as in a FULL job, same window by window path).

    Whisper takes its turn with the analyses (app.gpu_slot): `on_wait` and `check` as there.
    """
    from .gpu_slot import transcription_slot
    from .transcription import transcribe_windows
    from .worker import _extract_audio, get_whisper_model, release_whisper_model

    whisper = ai_models.whisper_model()
    cache = _transcript_cache(key, whisper)
    if cache.is_file():
        try:
            return json.loads(cache.read_text(encoding="utf-8")) | {"cached": True}
        except ValueError:
            pass
    cache.parent.mkdir(parents=True, exist_ok=True)
    audio = cache.parent / f".{key}.wav"
    started = time.monotonic()
    try:
        _extract_audio(media, audio)
        with transcription_slot(on_wait=on_wait, check=check):
            try:
                rows, language = transcribe_windows(
                    get_whisper_model(), audio, language=None, initial_prompt=None,
                    beam_size=settings.whisper_beam_size, on_progress=on_progress,
                )
            finally:
                # The slot is freed with the card's memory: the next transcription may load its model.
                release_whisper_model()
    finally:
        audio.unlink(missing_ok=True)
    result = {"rows": [list(row) for row in rows], "language": language, "seconds": round(time.monotonic() - started)}
    cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result | {"cached": False}


def run_quality(run_id: str) -> None:
    """RQ entry point (secondary queue): the whole run, saving the results item by item."""
    from .utils import timestamp
    from .worker import DEFAULT_TEMPLATE, compose_summary, release_whisper_model

    with SessionLocal() as db:
        run = db.get(QualityRun, run_id)
        if run is None or run.status != "QUEUED":
            return
        run.status = "RUNNING"
        db.commit()
        scope = run.scope
    results: dict = {}
    try:
        corpus = load_corpus()
        if corpus is None:
            raise RuntimeError("Corpus de référence absent (data/corpus)")
        sources, references = corpus
        keys = [key for key in SCOPES[scope] if key in sources]
        steps = max(1, 2 * len(keys))
        transcripts: dict[str, dict] = {}
        # Every transcription first, then Whisper leaves the GPU to the LLM (as in the pipeline).
        for index, key in enumerate(keys):
            media = corpus_dir() / sources[key].get("file", "")
            if not media.is_file():
                results[key] = {"error": "Fichier absent du corpus"}
                continue
            _update(run_id, current=f"Transcription : {key}", progress=int(100 * index / steps))
            duration = float(sources[key].get("duration_seconds") or 0)
            shown = [int(100 * index / steps)]

            def report(seconds: float, index=index, duration=duration, shown=shown) -> None:
                # Within the item's share of the run, updated every 2 %: a 3-hour file no longer looks stuck.
                value = int(100 * (index + min(1.0, seconds / duration if duration else 0)) / steps)
                if value >= shown[0] + 2:
                    shown[0] = value
                    _update(run_id, progress=value)

            transcripts[key] = transcript_for(
                key, media, on_progress=report,
                on_wait=lambda key=key: _update(run_id, current=f"En attente d'une autre transcription ({key})"),
                check=lambda: _update(run_id),
            )
        release_whisper_model()

        with SessionLocal() as db:
            prompts = _template_prompts(db)
        for index, key in enumerate(keys):
            if key not in transcripts:
                continue
            _update(run_id, current=f"Résumé : {key}", progress=int(100 * (len(keys) + index) / steps))
            transcript = transcripts[key]
            duration = float(sources[key].get("duration_seconds") or (transcript["rows"][-1][1] if transcript["rows"] else 0))
            text = "\n".join(f"[{timestamp(start)}] {line}" for start, _, line in transcript["rows"])
            started = time.monotonic()

            def check(stage, progress):
                with SessionLocal() as db:
                    if db.scalar(select(QualityRun.status).where(QualityRun.id == run_id)) == "CANCELLED":
                        raise RunCancelled(run_id)

            final, _, chapters, _ = compose_summary(
                source_text=text,
                output_language=analysis_options.language_name(transcript.get("language")) or "français",
                summary_length="standard",
                duration_seconds=duration,
                template_prompt=prompts.get(TEMPLATES.get(key, ""), DEFAULT_TEMPLATE),
                custom_prompt=None,
                vocabulary=[],
                on_stage=check,
            )
            results[key] = score_item(
                final, duration, chapters, references.get(key, {}), transcript.get("language"), transcript=text,
            ) | {
                "transcription_seconds": None if transcript.get("cached") else transcript.get("seconds"),
                "summary_seconds": round(time.monotonic() - started),
                "summary": final,
                "chapter_list": [[start, title] for start, title in chapters],
            }
            _update(run_id, results=json.dumps(results, ensure_ascii=False))
        _update(
            run_id, status="COMPLETED", progress=100, current=None, results=json.dumps(results, ensure_ascii=False),
            score=overall_score(results), finished_at=now(),
        )
    except RunCancelled:
        logger.info("Quality run %s cancelled", run_id)
    except Exception as exc:
        logger.exception("Quality run %s failed", run_id)
        try:
            _update(run_id, status="FAILED", error=str(exc)[:500] or "Échec", current=None, finished_at=now(),
                    results=json.dumps(results, ensure_ascii=False) if results else None)
        except RunCancelled:
            pass


def enqueue(scope: str = "quick", trigger: str = "manual") -> QualityRun:
    with SessionLocal() as db:
        print_, version = fingerprint(db)
        run = QualityRun(
            id=str(uuid.uuid4()), status="QUEUED", scope=scope, trigger=trigger,
            llm_model=ai_models.llm_model(), whisper_model=ai_models.whisper_model(),
            prompt_version=version, fingerprint=print_, progress=0,
        )
        db.add(run)
        db.commit()
    try:
        queue = Queue(INDEX_QUEUE_NAME, connection=Redis.from_url(settings.redis_url), default_timeout=21600)
        rq_job = queue.enqueue("app.quality.run_quality", run.id, job_timeout=21600, result_ttl=86400)
    except Exception:
        with SessionLocal() as db:
            stored = db.get(QualityRun, run.id)
            if stored:
                db.delete(stored)
                db.commit()
        raise
    with SessionLocal() as db:
        stored = db.get(QualityRun, run.id)
        stored.rq_job_id = rq_job.id
        db.commit()
        db.refresh(stored)
        db.expunge(stored)
        return stored


def maybe_schedule(trigger: str = "auto") -> str | None:
    """Start a quick run when the prompts or models changed since the last run; returns its id.

    A run of the current configuration that was cancelled or failed is not started
    again by itself (no loop at each worker start): the page offers to start it.
    """
    if not corpus_available():
        return None
    with SessionLocal() as db:
        if not app_settings.load(db, app_settings.QUALITY, QualitySettings).auto:
            return None
        if db.scalar(select(QualityRun.id).where(QualityRun.status.in_(ACTIVE)).limit(1)):
            return None
        current, _ = fingerprint(db)
        latest = db.scalar(select(QualityRun).order_by(QualityRun.created_at.desc()).limit(1))
        if latest is not None and latest.fingerprint == current:
            return None
    return enqueue("quick", trigger).id


def run_out(run: QualityRun, previous: QualityRun | None = None, *, detail: bool = False) -> dict:
    try:
        results = json.loads(run.results) if run.results else {}
    except ValueError:
        results = {}
    try:
        previous_results = json.loads(previous.results) if previous and previous.results else None
    except ValueError:
        previous_results = None
    items = {
        key: (value if detail else {name: v for name, v in value.items() if name not in ("summary", "chapter_list")})
        for key, value in results.items() if isinstance(value, dict)
    }
    return {
        "id": run.id, "status": run.status, "scope": run.scope, "trigger": run.trigger,
        "llm_model": run.llm_model, "whisper_model": run.whisper_model, "prompt_version": run.prompt_version,
        "progress": run.progress, "current": run.current, "score": run.score, "error": run.error,
        "created_at": run.created_at, "finished_at": run.finished_at,
        "previous_id": previous.id if previous else None,
        "previous_score": previous.score if previous else None,
        "changes": compare(results, previous_results) if run.status == "COMPLETED" and previous_results else [],
        "items": items,
    }
