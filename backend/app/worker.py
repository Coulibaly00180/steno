import gc
import hashlib
import json
import logging
import math
import re
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path

from redis import Redis
from rq import Queue
from sqlalchemy import delete, select

from .analysis_options import (
    effective_vocabulary,
    estimated_tokens,
    final_output_tokens,
    language_name,
    llm_terms,
    split_stored_terms,
    whisper_initial_prompt,
    word_budget,
)
from .config import INDEX_QUEUE_NAME, settings
from .db import SessionLocal
from .exports import write_exports
from .llm import final_summary, summarize_chunk, summarize_group, translate_chunk
from .models import Chapter, JobDuration, ProcessingJob, Summary, SummaryTemplate, TranscriptSegment, Video, VideoIndex
from .diarization import assign_speakers, diarize
from .retrieval import build_passages, embed_texts, record_index_failure, transcript_hash, write_index
from .speakers import apply_turns, build_transcript, speaker_labels
from .storage import StorageError, can_compact, compact_to_audio, delete_media
from .transcription import StallWatchdog, transcribe_windows
from .utils import ffprobe_duration, split_text, timestamp
from . import url_import

_whisper_model = None

logger = logging.getLogger(__name__)

TERMINAL_JOB_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}
TERMINAL_VIDEO_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}
# CANCELLED is only ever set by the API (POST /jobs/{id}/cancel).
JOB_TRANSITIONS = {
    "QUEUED": {"RUNNING", "FAILED", "CANCELLED"},
    "RUNNING": {"COMPLETED", "FAILED", "CANCELLED"},
}
VIDEO_TRANSITIONS = {
    "QUEUED": {"PROCESSING", "FAILED", "CANCELLED"},
    "PROCESSING": {"COMPLETED", "FAILED", "CANCELLED"},
}

ERROR_JOB_NOT_FOUND = "Job introuvable"
ERROR_VIDEO_NOT_FOUND = "Vidéo introuvable"
ERROR_SOURCE_NOT_FOUND = "Fichier source introuvable"
ERROR_PROCESSING_FAILED = "Échec du traitement vidéo"
ERROR_PROCESSING_INTERRUPTED = "Traitement interrompu avant sa fin"
ERROR_SUMMARY_FAILED = "Échec de la génération du résumé"
ERROR_TRANSCRIPTION_STALLED = "La transcription s'est bloquée ; relancez le traitement"
ERROR_INDEX_FAILED = "Échec de l'indexation pour les questions"
ERROR_DIARIZATION_FAILED = "Échec de l'identification des intervenants"
ERROR_COMPACT_FAILED = "Échec de la conversion en audio seul"
NO_SPEECH_SUMMARY = "Aucun contenu parlé détecté."
# Room kept in the context window for the final prompt's own instructions.
FINAL_PROMPT_OVERHEAD_TOKENS = 1024


class PipelineError(RuntimeError):
    """An expected processing failure safe to expose to the API client."""

    def __init__(self, public_message: str):
        super().__init__(public_message)
        self.public_message = public_message


class JobCancelled(RuntimeError):
    """The user cancelled the job: stop without recording a failure.

    The API stops the RQ work-horse at once; this is the fallback when that
    command is lost, raised at the next progress update or before a result is
    saved.
    """


def now():
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes: they are UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def check_cancelled(job_id: str) -> None:
    with SessionLocal() as db:
        if db.scalar(select(ProcessingJob.status).where(ProcessingJob.id == job_id)) == "CANCELLED":
            raise JobCancelled(job_id)


def _record_duration(db, job: ProcessingJob) -> None:
    """Keep how long the job took: queue estimates learn from it (n°13)."""
    video = db.get(Video, job.video_id)
    if video is None or job.started_at is None or not video.duration_seconds:
        return
    elapsed = (_aware(job.finished_at) - _aware(job.started_at)).total_seconds()
    if elapsed > 0:
        db.add(JobDuration(
            kind=job.kind or "FULL",
            media_seconds=video.duration_seconds,
            elapsed_seconds=elapsed,
            translated=bool(video.target_language),
            finished_at=job.finished_at,
        ))


def set_job(job_id: str, *, stage: str | None = None, status: str | None = None, progress: int | None = None, error: str | None = None) -> bool:
    """Update a non-terminal job, enforcing the worker's state transitions.

    Returning ``False`` means that the job does not exist, is terminal, or the
    requested status transition is invalid.  In particular, progress/stage
    updates never mutate a terminal job.
    """
    with SessionLocal() as db:
        job = db.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .with_for_update()
        )
        if not job:
            return False
        current_status = job.status
        if current_status == "CANCELLED":
            raise JobCancelled(job_id)
        if current_status in TERMINAL_JOB_STATUSES:
            return False
        if status is not None and status != current_status:
            if status not in JOB_TRANSITIONS.get(current_status, set()):
                return False
        if stage is not None:
            job.stage = stage
        if status is not None:
            job.status = status
        if progress is not None:
            job.progress = progress
        if error is not None:
            job.error = error
        if status == "RUNNING" and job.started_at is None:
            job.started_at = now()
        if status in {"COMPLETED", "FAILED"}:
            job.finished_at = now()
        if status == "COMPLETED":
            _record_duration(db, job)
        db.commit()
        return True


def _start_job(job_id: str) -> tuple[bool, str | None]:
    """Atomically claim a queued job for this worker invocation."""
    with SessionLocal() as db:
        job = db.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .with_for_update()
        )
        if not job:
            return False, None
        if job.status != "QUEUED":
            return False, job.video_id
        job.stage = "STARTING"
        job.status = "RUNNING"
        job.progress = 1
        job.started_at = now()
        db.commit()
        return True, job.video_id


def set_video_status(video_id: str, status: str) -> bool:
    """Set a video status while enforcing its lifecycle transitions.

    A missing video, a terminal video, or an invalid transition returns
    ``False``.  The row is locked for the duration of the update so a worker
    cannot race a deletion or another status update.
    """
    with SessionLocal() as db:
        video = db.scalar(
            select(Video)
            .where(Video.id == video_id)
            .with_for_update()
        )
        if not video or video.status in TERMINAL_VIDEO_STATUSES:
            return False
        if status != video.status and status not in VIDEO_TRANSITIONS.get(video.status, set()):
            return False
        video.status = status
        db.commit()
        return True


def mark_interrupted_job(job_id: str, public_error: str = ERROR_PROCESSING_INTERRUPTED) -> bool:
    """Atomically make an interrupted RQ job deletable and observable as failed."""
    with SessionLocal() as db:
        job = db.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.id == job_id)
            .with_for_update()
        )
        if not job or job.status in TERMINAL_JOB_STATUSES:
            return False
        video = db.scalar(
            select(Video)
            .where(Video.id == job.video_id)
            .with_for_update()
        )
        job.stage = "FAILED"
        job.status = "FAILED"
        job.error = public_error
        job.finished_at = now()
        if video and video.status not in TERMINAL_VIDEO_STATUSES:
            video.status = "FAILED"
        db.commit()
        return True


def recover_interrupted_jobs() -> int:
    """Fail work left RUNNING by a previous worker process in the single-worker MVP."""
    with SessionLocal() as db:
        jobs = list(db.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.status == "RUNNING")
            .with_for_update()
        ))
        for job in jobs:
            video = db.scalar(
                select(Video)
                .where(Video.id == job.video_id)
                .with_for_update()
            )
            job.stage = "FAILED"
            job.status = "FAILED"
            job.error = ERROR_PROCESSING_INTERRUPTED
            job.finished_at = now()
            if video and video.status not in TERMINAL_VIDEO_STATUSES:
                video.status = "FAILED"
        if jobs:
            db.commit()
        return len(jobs)


def _record_failure(job_id: str, video_id: str | None, public_error: str, exc: BaseException) -> None:
    """Persist a stable client-facing error and keep technical details in logs."""
    logger.exception("Video processing failed for job %s: %s", job_id, public_error)
    try:
        set_job(job_id, stage="FAILED", status="FAILED", error=public_error)
    except JobCancelled:
        # Cancelled meanwhile: the failure is a consequence (a video left CANCELLED…).
        return
    if video_id:
        set_video_status(video_id, "FAILED")


def get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        _whisper_model = WhisperModel(
            settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        )
    return _whisper_model


def release_whisper_model() -> None:
    """Free Whisper's GPU memory before the LLM stages.

    Measured on an RTX 5080: Whisper kept 2.3 GB of VRAM through the summary
    steps. Next to Ollama's 32k context the card overflowed into shared system
    memory and the block summaries of a 3-hour conference took 58 minutes
    instead of 2. RQ runs every job in a fresh process, so keeping the model
    loaded never saved a reload anyway.
    """
    global _whisper_model
    if _whisper_model is None:
        return
    try:
        _whisper_model.model.unload_model()
    except Exception:
        logger.warning("Unable to unload the Whisper model", exc_info=True)
    _whisper_model = None
    gc.collect()


def final_input_budget(num_predict: int) -> int:
    """Tokens left for the block summaries in the final prompt (F-5.5)."""
    available = settings.llm_num_ctx - num_predict - FINAL_PROMPT_OVERHEAD_TOKENS
    return max(1024, min(settings.llm_num_ctx // 2, available))


def _group_consecutive(items: list[str], budget_tokens: int) -> list[list[str]]:
    """Consecutive groups within the budget, at least two items each so every pass shrinks the list."""
    groups: list[list[str]] = []
    current: list[str] = []
    for item in items:
        candidate = [*current, item]
        if current and len(current) >= 2 and estimated_tokens("\n\n".join(candidate)) > budget_tokens:
            groups.append(current)
            current = [item]
        else:
            current = candidate
    if current:
        if len(current) == 1 and groups:
            groups[-1].append(current[0])
        else:
            groups.append(current)
    return groups


def reduce_block_summaries(
    blocks: list[str],
    output_language: str,
    *,
    budget_tokens: int,
    detailed: bool,
    vocabulary: list[str],
    on_group_done=None,
) -> list[str]:
    """Merge consecutive block summaries until they fit the final prompt."""
    level = blocks
    while len(level) > 1 and estimated_tokens("\n\n".join(level)) > budget_tokens:
        groups = _group_consecutive(level, budget_tokens)
        merged = []
        for index, group in enumerate(groups, 1):
            joined = "\n\n".join(group)
            text = summarize_group(joined, output_language, detailed=detailed, vocabulary=vocabulary)
            merged.append(f"{_range_heading(chunk_time_range(joined))}\n{text}")
            if on_group_done:
                on_group_done(index, len(groups))
        level = merged
    return level


_BRACKET_CLOCK = re.compile(r"\[(\d{1,2}:\d{2}:\d{2})\]")
# Two chapters closer than this are one topic change seen twice (at a block
# boundary). Kept small: in a short video, real topics can be 15 s apart.
MIN_CHAPTER_GAP_SECONDS = 10


def parse_clock(value: str) -> float | None:
    parts = value.split(":")
    if not all(part.isdigit() for part in parts) or len(parts) not in (2, 3):
        return None
    numbers = [int(part) for part in parts]
    if any(n >= 60 for n in numbers[1:]):
        return None
    seconds = 0
    for number in numbers:
        seconds = seconds * 60 + number
    return float(seconds)


def chunk_time_range(text: str) -> tuple[float, float] | None:
    clocks = [parse_clock(value) for value in _BRACKET_CLOCK.findall(text)]
    clocks = [clock for clock in clocks if clock is not None]
    return (min(clocks), max(clocks)) if clocks else None


def _range_heading(time_range: tuple[float, float] | None) -> str:
    """Label of an intermediate summary: its time span, never a word the model could repeat."""
    if time_range is None:
        return "###"
    return f"### [{timestamp(time_range[0])}] → [{timestamp(time_range[1])}]"


def valid_chapters(raw: list[tuple[str, str]], time_range: tuple[float, float] | None) -> list[tuple[float, str]]:
    """Keep chapters whose timestamp exists within the block: the model may invent times."""
    if time_range is None:
        return []
    low, high = time_range
    chapters = []
    for clock, title in raw:
        start = parse_clock(clock)
        if start is not None and low - 1 <= start <= high + 1 and title:
            chapters.append((start, title))
    return chapters


def chapter_limit(duration_seconds: float) -> int:
    """How many chapters a video deserves: ~3 x sqrt(minutes), between 3 and 40.

    4 for 90 s, 10 for 11 min, 23 for 1 h, 40 for 3 h and more. The model is
    left free to be detailed; this caps the result (it tends to split one
    subject into "presentation of X" and "analysis of X").
    """
    return max(3, min(40, round(3 * math.sqrt(max(0.0, duration_seconds) / 60))))


def keep_main_chapters(chapters: list[tuple[float, str]], limit: int) -> list[tuple[float, str]]:
    """Drop the chapter starting soonest after its predecessor until `limit` remain.

    A chapter that follows its predecessor closely is usually the same subject:
    it merges into it. The first chapter is always kept.
    """
    kept = sorted(chapters)
    while len(kept) > limit:
        closest = min(range(1, len(kept)), key=lambda index: kept[index][0] - kept[index - 1][0])
        del kept[closest]
    return kept


def block_chapters(raw: list[tuple[str, str]], block_text: str) -> list[tuple[float, str]]:
    """Chapters of one block whose timestamps exist within the block."""
    return valid_chapters(raw, chunk_time_range(block_text))


def merge_chapters(chapters: list[tuple[float, str]]) -> list[tuple[float, str]]:
    merged: list[tuple[float, str]] = []
    for start, title in sorted(chapters):
        if merged and (start - merged[-1][0] < MIN_CHAPTER_GAP_SECONDS or title.casefold() == merged[-1][1].casefold()):
            continue
        merged.append((start, title))
    return merged


def summary_cache_key(source_text: str, output_language: str, detailed: bool) -> str:
    digest = hashlib.sha256(source_text.encode("utf-8")).hexdigest()
    return f"{digest}|{output_language}|{'detailed' if detailed else 'normal'}"


def _load_template(template_id: str | None) -> tuple[str, str | None, str | None]:
    """Template prompt, name and id actually used (falls back to the default)."""
    with SessionLocal() as db:
        tpl = db.get(SummaryTemplate, template_id) if template_id else None
        if tpl is None:
            tpl = db.scalar(select(SummaryTemplate).where(SummaryTemplate.is_default.is_(True)))
        if tpl is None:
            return DEFAULT_TEMPLATE, None, None
        return tpl.prompt, tpl.name, tpl.id


def summarize_transcript(
    job_id: str,
    video_id: str,
    *,
    source_text: str,
    output_language: str,
    summary_length: str,
    duration_seconds: float,
    template_id: str | None,
    custom_prompt: str | None,
    vocabulary: list[str],
    language_code: str | None,
) -> None:
    """Block summaries and chapters (cached), reduction, final summary; persist all."""
    detailed = summary_length == "detailed"
    budget = word_budget(duration_seconds, summary_length)
    key = summary_cache_key(source_text, output_language, detailed)

    with SessionLocal() as db:
        video = db.get(Video, video_id)
        if not video:
            raise PipelineError(ERROR_VIDEO_NOT_FOUND)
        try:
            cache = json.loads(video.summary_cache) if video.summary_cache else None
        except ValueError:
            cache = None

    if cache and cache.get("key") == key:
        # Same text at the same detail level: only the final summary changes.
        blocks = list(cache["blocks"])
        chapters = [(float(start), title) for start, title in cache["chapters"]]
        set_job(job_id, stage="SUMMARIZING_CHUNKS", progress=85)
    else:
        set_job(job_id, stage="SUMMARIZING_CHUNKS", progress=72)
        blocks, found = [], []
        chunks = split_text(source_text, settings.summary_chunk_chars)
        total = max(1, len(chunks))
        for idx, chunk in enumerate(chunks, 1):
            result = summarize_chunk(chunk, output_language, detailed=detailed, vocabulary=vocabulary)
            blocks.append(f"{_range_heading(chunk_time_range(chunk))}\n{result.text}")
            found.extend(block_chapters(result.chapters, chunk))
            set_job(job_id, progress=72 + int(13 * idx / total))
        chapters = keep_main_chapters(merge_chapters(found), chapter_limit(duration_seconds))

    summaries = blocks
    input_budget = final_input_budget(final_output_tokens(budget))
    if len(summaries) > 1 and estimated_tokens("\n\n".join(summaries)) > input_budget:
        set_job(job_id, stage="SUMMARIZING_GROUPS", progress=85)
        summaries = reduce_block_summaries(
            summaries,
            output_language,
            budget_tokens=input_budget,
            detailed=detailed,
            vocabulary=vocabulary,
            on_group_done=lambda done, count: set_job(job_id, progress=85 + int(5 * done / count)),
        )

    # Additional instructions complement the template, they never replace it (F-T.2).
    template_prompt, _, actual_template_id = _load_template(template_id)

    set_job(job_id, stage="SUMMARIZING_FINAL", progress=90)
    if summaries:
        final = final_summary(
            "\n\n".join(summaries),
            template_prompt,
            output_language,
            word_budget=budget,
            instructions=custom_prompt,
            vocabulary=vocabulary,
        )
    else:
        # Nothing was said: asking the LLM would only invite invention.
        final = NO_SPEECH_SUMMARY

    check_cancelled(job_id)
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        if not video:
            raise PipelineError(ERROR_VIDEO_NOT_FOUND)
        db.add(Summary(
            id=str(uuid.uuid4()),
            video_id=video.id,
            template_id=actual_template_id,
            language=language_code,
            summary_length=summary_length,
            content_markdown=final,
            model=settings.llm_model,
        ))
        db.execute(delete(Chapter).where(Chapter.video_id == video.id))
        db.add_all([Chapter(video_id=video.id, start_seconds=start, title=title) for start, title in chapters])
        video.summary_cache = json.dumps({"key": key, "blocks": blocks, "chapters": chapters}, ensure_ascii=False)
        db.commit()


def translate_transcript(job_id: str, video_id: str, transcript: str, target_language: str, vocabulary: list[str]) -> str:
    set_job(job_id, stage="TRANSLATING", progress=56)
    translated_chunks = []
    chunks = split_text(transcript, settings.translation_chunk_chars)
    total = max(1, len(chunks))
    for idx, chunk in enumerate(chunks, 1):
        translated_chunks.append(translate_chunk(chunk, target_language, vocabulary=vocabulary))
        set_job(job_id, progress=56 + int(14 * idx / total))
    translated = "\n\n".join(translated_chunks)
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        if not video:
            raise PipelineError(ERROR_VIDEO_NOT_FOUND)
        video.translated_text = translated
        video.translated_at = now()
        db.commit()
    return translated


def _write_exports(job_id: str, video_id: str) -> None:
    set_job(job_id, stage="GENERATING_EXPORTS", progress=96)
    with SessionLocal() as db:
        write_exports(db, video_id)


def index_video(video_id: str, on_progress=None, job_id: str | None = None) -> int:
    """Embed the video's passages and store them; returns how many (n°6).

    Embeddings are computed outside any transaction; they are saved only if
    the transcript has not been corrected meanwhile (a newer job re-indexes it).
    """
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        if not video or not video.transcript_text:
            raise PipelineError(ERROR_VIDEO_NOT_FOUND if not video else "La transcription n'est pas disponible")
        source_hash = transcript_hash(video.transcript_text)
        labels = speaker_labels(video)
        drafts = build_passages(
            (s.start_seconds, s.end_seconds, f"{labels[s.speaker_id]} : {s.text}" if s.speaker_id in labels else s.text)
            for s in video.segments
        )
    vectors = embed_texts([draft.plain for draft in drafts], on_batch=on_progress) if drafts else []
    if job_id:
        check_cancelled(job_id)
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        if not video:
            raise PipelineError(ERROR_VIDEO_NOT_FOUND)
        if transcript_hash(video.transcript_text) != source_hash:
            logger.info("Transcript of %s changed during indexing; left to the next index job", video_id)
            return 0
        write_index(db, video_id, drafts, vectors, source_hash)
        db.commit()
    return len(drafts)


def _index_after_pipeline(job_id: str, video_id: str) -> None:
    """The chat works without the index (on the start of a long transcript): never fail the video for it."""
    set_job(job_id, stage="INDEXING", progress=98)
    try:
        index_video(video_id, job_id=job_id)
    except JobCancelled:
        raise
    except Exception as exc:
        logger.warning("Indexing failed for video %s", video_id, exc_info=True)
        with SessionLocal() as db:
            video = db.get(Video, video_id)
            if video:
                record_index_failure(db, video_id, transcript_hash(video.transcript_text), str(exc)[:500])
                db.commit()


def _extract_audio(video_path: Path, audio_path: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(video_path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio_path)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=settings.ffmpeg_timeout_seconds,
    )


def diarize_video(video_id: str, *, on_progress=None, job_id: str | None = None) -> int:
    """Find the speakers, label every segment, rebuild the transcript; returns the speaker count."""
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        if not video:
            raise PipelineError(ERROR_VIDEO_NOT_FOUND)
        audio_path = settings.audio_dir / f"{video.id}.wav"
        video_path, num_speakers = Path(video.path), video.num_speakers
    if not audio_path.is_file():
        if not video_path.is_file():
            raise PipelineError(ERROR_SOURCE_NOT_FOUND)
        settings.audio_dir.mkdir(parents=True, exist_ok=True)
        _extract_audio(video_path, audio_path)
    turns = diarize(audio_path, num_speakers=num_speakers, on_progress=on_progress)
    if job_id:
        check_cancelled(job_id)
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        if not video:
            raise PipelineError(ERROR_VIDEO_NOT_FOUND)
        numbers = assign_speakers([(s.start_seconds, s.end_seconds) for s in video.segments], turns)
        apply_turns(db, video, numbers)
        db.flush()
        db.refresh(video)
        video.transcript_text = build_transcript(video)
        video.diarization_error = None
        db.commit()
        return len(video.speakers)


def _diarize_in_pipeline(job_id: str, video_id: str, duration: float) -> None:
    """Speakers are a bonus: a failure never fails the video (the transcript stays unlabelled)."""
    set_job(job_id, stage="DIARIZING", progress=50)
    try:
        diarize_video(
            video_id,
            on_progress=lambda seconds: set_job(job_id, progress=50 + int(2 * seconds / duration)) if duration else None,
            job_id=job_id,
        )
    except JobCancelled:
        raise
    except Exception as exc:
        logger.warning("Diarization failed for video %s", video_id, exc_info=True)
        with SessionLocal() as db:
            video = db.get(Video, video_id)
            if video:
                video.diarization_error = str(exc)[:500] or ERROR_DIARIZATION_FAILED
                db.commit()


def run_diarize(job_id: str) -> None:
    """DIARIZE job: identify the speakers of an already processed video (n°8).

    The video stays COMPLETED; its summary is flagged as older than the
    transcript (now with speakers), so the page offers to regenerate it.
    """
    video_id = None
    if not _claim(job_id):
        return
    try:
        with SessionLocal() as db:
            job = db.get(ProcessingJob, job_id)
            video_id = job.video_id if job else None
            video = db.get(Video, video_id) if video_id else None
            duration = video.duration_seconds if video else 0
        if video_id is None:
            raise PipelineError(ERROR_JOB_NOT_FOUND)
        set_job(job_id, stage="DIARIZING", progress=5)
        diarize_video(
            video_id,
            on_progress=lambda seconds: set_job(job_id, progress=5 + int(85 * seconds / duration)) if duration else None,
            job_id=job_id,
        )
        with SessionLocal() as db:
            video = db.get(Video, video_id)
            video.transcript_edited_at = now()
            index = db.get(VideoIndex, video_id)
            if index:
                index.status = "STALE"
            db.commit()
        _write_exports(job_id, video_id)
        enqueue_index_job(video_id)
        set_job(job_id, stage="COMPLETED", status="COMPLETED", progress=100)
    except JobCancelled:
        logger.info("Diarization job %s cancelled", job_id)
    except Exception as exc:
        logger.exception("Diarization job %s failed", job_id)
        public = exc.public_message if isinstance(exc, PipelineError) else ERROR_DIARIZATION_FAILED
        try:
            # Only the job fails: the video and its transcript are untouched.
            set_job(job_id, stage="FAILED", status="FAILED", error=public)
        except JobCancelled:
            pass


def enqueue_index_job(video_id: str) -> str | None:
    """Queue an INDEX job unless one is already waiting for this video."""
    with SessionLocal() as db:
        waiting = db.scalar(
            select(ProcessingJob.id).where(
                ProcessingJob.video_id == video_id, ProcessingJob.kind == "INDEX", ProcessingJob.status == "QUEUED"
            )
        )
        if waiting:
            return waiting
        job = ProcessingJob(id=str(uuid.uuid4()), video_id=video_id, kind="INDEX", stage="QUEUED", status="QUEUED", progress=0)
        db.add(job)
        db.commit()
    try:
        queue = Queue(INDEX_QUEUE_NAME, connection=Redis.from_url(settings.redis_url), default_timeout=21600)
        rq_job = queue.enqueue("app.worker.run_index", job.id, job_timeout=21600, result_ttl=86400)
    except Exception:
        logger.warning("Unable to enqueue index job for video %s", video_id, exc_info=True)
        with SessionLocal() as db:
            db.execute(delete(ProcessingJob).where(ProcessingJob.id == job.id))
            db.commit()
        return None
    with SessionLocal() as db:
        stored = db.get(ProcessingJob, job.id)
        if stored:
            stored.rq_job_id = rq_job.id
            db.commit()
    return job.id


def enqueue_missing_indexes() -> int:
    """Catch-up (n°6): every processed video whose passages are missing, stale or from another model."""
    with SessionLocal() as db:
        indexed = select(VideoIndex.video_id).where(
            VideoIndex.status == "READY", VideoIndex.model == settings.embedding_model
        )
        waiting = select(ProcessingJob.video_id).where(
            ProcessingJob.kind == "INDEX", ProcessingJob.status.in_(("QUEUED", "RUNNING"))
        )
        video_ids = list(db.scalars(
            select(Video.id)
            .where(Video.status == "COMPLETED", Video.transcript_text.is_not(None))
            .where(Video.id.not_in(indexed), Video.id.not_in(waiting))
            .order_by(Video.created_at.desc())
        ))
    return sum(1 for video_id in video_ids if enqueue_index_job(video_id))


def run_index(job_id: str) -> None:
    """INDEX job: (re)build a video's passages, e.g. after a transcript correction."""
    video_id = None
    if not _claim(job_id):
        return
    try:
        with SessionLocal() as db:
            job = db.get(ProcessingJob, job_id)
            video_id = job.video_id if job else None
        if video_id is None:
            raise PipelineError(ERROR_JOB_NOT_FOUND)
        set_job(job_id, stage="INDEXING", progress=5)
        index_video(
            video_id,
            on_progress=lambda done, total: set_job(job_id, progress=5 + int(90 * done / total)),
            job_id=job_id,
        )
        set_job(job_id, stage="COMPLETED", status="COMPLETED", progress=100)
    except JobCancelled:
        logger.info("Index job %s cancelled", job_id)
    except Exception as exc:
        logger.exception("Index job %s failed", job_id)
        if video_id:
            with SessionLocal() as db:
                video = db.get(Video, video_id)
                if video:
                    record_index_failure(db, video_id, transcript_hash(video.transcript_text), str(exc)[:500])
                    db.commit()
        try:
            # The video itself is untouched: only this job fails.
            set_job(job_id, stage="FAILED", status="FAILED", error=ERROR_INDEX_FAILED)
        except JobCancelled:
            pass


def _claim(job_id: str) -> bool:
    claimed, video_id = _start_job(job_id)
    if not claimed:
        if video_id is None:
            logger.warning("Ignoring processing request for missing job %s", job_id)
        else:
            logger.info("Ignoring duplicate or terminal processing request for job %s", job_id)
    return claimed


def run_pipeline(job_id: str) -> None:
    """FULL job: audio extraction, transcription, translation, summary, exports."""
    video_id = None
    if not _claim(job_id):
        return
    try:
        with SessionLocal() as db:
            job = db.get(ProcessingJob, job_id)
            if not job:
                raise PipelineError(ERROR_JOB_NOT_FOUND)
            video_id = job.video_id
            video = db.get(Video, job.video_id)
            if not video:
                raise PipelineError(ERROR_VIDEO_NOT_FOUND)
            if video.status in TERMINAL_VIDEO_STATUSES:
                raise PipelineError("Vidéo déjà terminée")
            video_path = Path(video.path)
            source_url = video.source_url
            if not video_path.is_file() and not source_url:
                raise PipelineError(ERROR_SOURCE_NOT_FOUND)
            audio_path = settings.audio_dir / f"{video.id}.wav"
            target_language = video.target_language
            template_id = job.template_id
            custom_prompt = job.custom_prompt
            summary_length = job.summary_length or "standard"
            video_duration = video.duration_seconds
            transcript = video.transcript_text
            source_language = video.detected_language
            forced_language = video.detected_language if video.source_language_forced else None
            video_terms = split_stored_terms(video.vocabulary)
            glossary_terms = split_stored_terms(video.glossary_snapshot)
            wants_speakers = video.diarize and not video.speakers
        vocabulary = effective_vocabulary(video_terms, glossary_terms)
        prompt_vocabulary = llm_terms(vocabulary)

        if not set_video_status(video_id, "PROCESSING"):
            raise PipelineError("Vidéo déjà terminée ou transition invalide")

        if not video_path.is_file():
            # Imported from a link (n°12): fetch the file first.
            video_path, video_duration = download_source(job_id, video_id, source_url)

        settings.exports_dir.mkdir(parents=True, exist_ok=True)
        if not transcript:
            # An interrupted job may already have committed its transcript: a
            # retry then resumes at translation/summarisation.
            settings.audio_dir.mkdir(parents=True, exist_ok=True)
            set_job(job_id, stage="EXTRACTING_AUDIO", progress=7)
            _extract_audio(video_path, audio_path)

            set_job(job_id, stage="TRANSCRIBING", progress=15)
            last_progress = 15

            def report(seconds: float) -> None:
                nonlocal last_progress
                if video_duration > 0:
                    p = min(50, 15 + int(35 * seconds / video_duration))
                    if p >= last_progress + 2:
                        set_job(job_id, progress=p)
                        last_progress = p

            model = get_whisper_model()
            watchdog = StallWatchdog(
                settings.whisper_stall_timeout_seconds,
                on_stall=lambda: mark_interrupted_job(job_id, ERROR_TRANSCRIPTION_STALLED),
            )
            # Window by window: decoding a whole 6-hour file at once needs ~5 GB of RAM.
            with watchdog:
                rows, detected = transcribe_windows(
                    model,
                    audio_path,
                    language=forced_language,
                    initial_prompt=whisper_initial_prompt(vocabulary),
                    beam_size=settings.whisper_beam_size,
                    on_progress=report,
                    heartbeat=watchdog.beat,
                )
            release_whisper_model()
            transcript = "\n".join(f"[{timestamp(start)}] {text}" for start, _, text in rows)
            source_language = forced_language or detected
            check_cancelled(job_id)

            with SessionLocal() as db:
                video = db.get(Video, video_id)
                if not video:
                    raise PipelineError(ERROR_VIDEO_NOT_FOUND)
                video.detected_language = source_language
                video.transcript_text = transcript
                db.execute(delete(TranscriptSegment).where(TranscriptSegment.video_id == video.id))
                db.add_all([
                    TranscriptSegment(video_id=video.id, start_seconds=s, end_seconds=e, text=t)
                    for s, e, t in rows
                ])
                db.commit()

        if wants_speakers and transcript:
            _diarize_in_pipeline(job_id, video_id, video_duration)
            with SessionLocal() as db:
                transcript = db.get(Video, video_id).transcript_text

        set_job(job_id, stage="TRANSCRIBED", progress=52)

        source_for_summary = transcript
        if target_language:
            source_for_summary = translate_transcript(job_id, video_id, transcript, target_language, prompt_vocabulary)

        summarize_transcript(
            job_id,
            video_id,
            source_text=source_for_summary,
            output_language=language_name(target_language or source_language) or "français",
            summary_length=summary_length,
            duration_seconds=video_duration,
            template_id=template_id,
            custom_prompt=custom_prompt,
            vocabulary=prompt_vocabulary,
            language_code=target_language or source_language,
        )
        _write_exports(job_id, video_id)
        _index_after_pipeline(job_id, video_id)

        # A video is only terminally successful once every export has been
        # written successfully.  This keeps deletion/status decisions safe.
        if not set_video_status(video_id, "COMPLETED"):
            raise PipelineError(ERROR_VIDEO_NOT_FOUND)
        _apply_source_policy(job_id, video_id)
        set_job(job_id, stage="COMPLETED", status="COMPLETED", progress=100)
    except JobCancelled:
        logger.info("Job %s cancelled by the user", job_id)
    except PipelineError as exc:
        _record_failure(job_id, video_id, exc.public_message, exc)
        raise
    except Exception as exc:
        _record_failure(job_id, video_id, ERROR_PROCESSING_FAILED, exc)
        raise


def download_source(job_id: str, video_id: str, url: str) -> tuple[Path, float]:
    """Download the media of a link import, probe it, record it on the video (n°12)."""
    set_job(job_id, stage="DOWNLOADING", progress=2)
    last = [2]

    def progress(done: int, total: int | None) -> None:
        if total:
            value = 2 + int(5 * done / total)
            if value > last[0]:
                last[0] = value
                set_job(job_id, progress=value)

    try:
        path, filename = url_import.download(url, settings.uploads_dir, video_id, on_progress=progress)
    except url_import.UrlImportError as exc:
        raise PipelineError(str(exc)) from exc
    try:
        duration = ffprobe_duration(path, timeout_seconds=settings.ffprobe_timeout_seconds)
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise PipelineError("Fichier téléchargé illisible : ce n'est pas un média valide") from exc
    if duration > settings.max_video_hours * 3600:
        path.unlink(missing_ok=True)
        raise PipelineError(f"Durée maximale: {settings.max_video_hours:g} heures")
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        if not video:
            path.unlink(missing_ok=True)
            raise PipelineError(ERROR_VIDEO_NOT_FOUND)
        video.path, video.filename = str(path), path.name
        video.duration_seconds, video.size_bytes = duration, path.stat().st_size
        if not video.original_filename:
            video.original_filename = filename
        db.commit()
    check_cancelled(job_id)
    return path, duration


def _apply_source_policy(job_id: str, video_id: str) -> None:
    """The media rule chosen at import (n°14). The text is safe: a failure only keeps the media."""
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        if video is None or video.source_policy == "keep":
            return
        policy, source = video.source_policy, Path(video.path)
    try:
        if policy == "audio" and can_compact(source):
            set_job(job_id, stage="COMPACTING", progress=99)
            compact_to_audio(video_id)
        elif policy == "delete":
            with SessionLocal() as db:
                video = db.get(Video, video_id)
                if video:
                    delete_media(video)
    except JobCancelled:
        raise
    except Exception:
        logger.warning("Media rule %s failed for video %s; media kept", policy, video_id, exc_info=True)


def run_compact(job_id: str) -> None:
    """COMPACT job: keep only a compact audio track of a processed video (n°14)."""
    if not _claim(job_id):
        return
    try:
        with SessionLocal() as db:
            job = db.get(ProcessingJob, job_id)
            video_id = job.video_id if job else None
        if video_id is None:
            raise PipelineError(ERROR_JOB_NOT_FOUND)
        set_job(job_id, stage="COMPACTING", progress=10)
        check_cancelled(job_id)
        compact_to_audio(video_id)
        set_job(job_id, stage="COMPLETED", status="COMPLETED", progress=100)
    except JobCancelled:
        logger.info("Compact job %s cancelled", job_id)
    except Exception as exc:
        logger.exception("Compact job %s failed", job_id)
        public = str(exc) if isinstance(exc, StorageError) else exc.public_message if isinstance(exc, PipelineError) else ERROR_COMPACT_FAILED
        try:
            # Only the job fails: the video and its media are untouched.
            set_job(job_id, stage="FAILED", status="FAILED", error=public)
        except JobCancelled:
            pass


def run_summary(job_id: str) -> None:
    """SUMMARY job: new summary of an existing transcript (n°9).

    The video stays COMPLETED throughout: its previous summary remains usable,
    and a failure only fails this job.
    """
    video_id = None
    if not _claim(job_id):
        return
    try:
        with SessionLocal() as db:
            job = db.get(ProcessingJob, job_id)
            if not job:
                raise PipelineError(ERROR_JOB_NOT_FOUND)
            video = db.get(Video, job.video_id)
            if not video:
                raise PipelineError(ERROR_VIDEO_NOT_FOUND)
            video_id = video.id
            if video.status != "COMPLETED" or not video.transcript_text:
                raise PipelineError("La transcription n'est pas disponible")
            transcript = video.transcript_text
            target_language = video.target_language
            source_language = video.detected_language
            translation = video.translated_text
            # A translation made before the latest transcript correction is stale.
            translation_fresh = bool(translation) and (
                video.transcript_edited_at is None
                or (video.translated_at is not None and video.translated_at >= video.transcript_edited_at)
            )
            template_id, custom_prompt = job.template_id, job.custom_prompt
            summary_length = job.summary_length or "standard"
            duration = video.duration_seconds
            vocabulary = llm_terms(effective_vocabulary(
                split_stored_terms(video.vocabulary), split_stored_terms(video.glossary_snapshot)
            ))

        source_for_summary = transcript
        if target_language:
            source_for_summary = translation if translation_fresh else translate_transcript(
                job_id, video_id, transcript, target_language, vocabulary
            )

        summarize_transcript(
            job_id,
            video_id,
            source_text=source_for_summary,
            output_language=language_name(target_language or source_language) or "français",
            summary_length=summary_length,
            duration_seconds=duration,
            template_id=template_id,
            custom_prompt=custom_prompt,
            vocabulary=vocabulary,
            language_code=target_language or source_language,
        )
        _write_exports(job_id, video_id)
        set_job(job_id, stage="COMPLETED", status="COMPLETED", progress=100)
    except JobCancelled:
        logger.info("Job %s cancelled by the user", job_id)
    except PipelineError as exc:
        _record_failure(job_id, video_id, exc.public_message, exc)
        raise
    except Exception as exc:
        _record_failure(job_id, video_id, ERROR_SUMMARY_FAILED, exc)
        raise


DEFAULT_TEMPLATE = """# Résumé exécutif
Un résumé court de l'essentiel.

# Points clés
Liste structurée des faits et idées importantes.

# Décisions
Décisions explicites prises dans le contenu. Si aucune, l'indiquer.

# Actions
Actions à réaliser, responsables et échéances lorsqu'ils sont mentionnés.

# Détails
Résumé plus complet et organisé par thème, sans inventer d'information.
"""
