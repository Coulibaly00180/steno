import asyncio
import json
import mimetypes
import re
import shutil
import unicodedata
import uuid
import logging
from urllib.parse import quote
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from redis import Redis
from rq import Queue
from rq.command import send_stop_job_command
from rq.job import Job as RQJob
from sqlalchemy import and_, case, delete, desc, exists, func, literal_column, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import load_only, selectinload

from .analysis_options import (
    DEFAULT_SUMMARY_LENGTH,
    SOURCE_LANGUAGES,
    SUMMARY_LENGTHS,
    TermsError,
    chat_fallback_language,
    effective_vocabulary,
    join_terms,
    llm_terms,
    parse_glossary,
    parse_vocabulary,
    split_stored_terms,
    whisper_terms,
)
from .config import QUEUE_NAME, settings
from .retrieval import Hit, embed_texts, merge_hits, ready_video_ids, retrieval_query, search
from .db import SessionLocal, engine
from .exports import EXPORT_NAMES, write_exports
from .llm import (
    CHAT_MAX_OUTPUT_TOKENS,
    CHAT_TEMPERATURE,
    answer_video_question,
    library_answer_prompt,
    stream_chat,
    video_answer_prompt,
)
from .models import (
    GlossaryTerm,
    LibraryConversation,
    LibraryMessage,
    ProcessingJob,
    Summary,
    SummaryTemplate,
    Tag,
    TranscriptSegment,
    Video,
    Speaker,
    VideoChatMessage,
    VideoIndex,
    utcnow,
    video_tags,
)
from .reports import build_report, render_docx, render_pdf
from .reports import TRANSCRIPT_MODES
from .speakers import build_transcript, relabel_translation, speakers_payload
from .queue_info import ACTIVE_JOB_STATUSES, QueueInfo, queue_snapshot
from .schema import assert_schema_current
from .schemas import (
    ChatMessageOut,
    SegmentOut,
    SummaryOut,
    ChatQuestion,
    DiarizeIn,
    LibraryConversationDetail,
    LibraryConversationOut,
    LibraryQuestion,
    SpeakerMergeIn,
    SpeakerRenameIn,
    GlossaryIn,
    GlossaryOut,
    JobOut,
    RegenerateIn,
    ReplaceIn,
    ReplaceOut,
    SegmentEditIn,
    SummaryEditIn,
    TagCount,
    TagsIn,
    TagsOut,
    TemplateCreate,
    TemplateOut,
    VideoDetail,
    VideoListItem,
)
from .status import system_status
from .utils import ffprobe_duration, timestamp
from .worker import DEFAULT_TEMPLATE, enqueue_index_job

logger = logging.getLogger(__name__)
UPLOAD_CHUNK_SIZE = 1024 * 1024
CHAT_HISTORY_LIMIT = 12
CHAT_CONTEXT_LIMIT = 30000
# Beyond CHAT_CONTEXT_LIMIT, the chat reads the passages closest to the question (n°6).
CHAT_PASSAGES = 12
LIBRARY_PASSAGES = 12
LIBRARY_PASSAGES_PER_VIDEO = 4
LIBRARY_MAX_VIDEOS = 500
# Background jobs of the semantic index: never "the video's job" in the interface.
INDEX_KIND = "INDEX"
CUSTOM_PROMPT_MAX_CHARS = 2000
DEFAULT_TEMPLATE_NAME = "Compte-rendu de réunion"
TERMINAL_JOB_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")
# Ogg formats are what Wikimedia Commons and many free podcasts publish; ffmpeg reads them.
AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".flac", ".ogg", ".oga", ".opus"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi", ".ogv"}
ERROR_JOB_IN_PROGRESS = "Un traitement est en cours pour cette vidéo ; réessayez à sa fin"
ERROR_CHAT_UNAVAILABLE = "Assistant indisponible, réessayez ultérieurement"
# Library filters (n°17): "ACTIVE" groups the videos waiting for or under processing.
LIBRARY_STATUS_FILTERS = {
    "COMPLETED": ("COMPLETED",),
    "FAILED": ("FAILED",),
    "CANCELLED": ("CANCELLED",),
    "ACTIVE": ("QUEUED", "PROCESSING"),
}
SEARCH_MAX_WORDS = 8
TAG_MAX_CHARS = 40
MAX_SPEAKERS = 20
REPORT_TYPES = {
    "report.docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", render_docx),
    "report.pdf": ("application/pdf", render_pdf),
}
TAGS_PER_VIDEO = 20


def _is_template_name_conflict(exc: IntegrityError) -> bool:
    """Recognize only the unique constraint for SummaryTemplate.name."""
    detail = str(getattr(exc, "orig", exc)).lower()
    return (
        "summary_templates.name" in detail
        or "summary_templates_name_key" in detail
        or ("unique" in detail and "template" in detail and "name" in detail)
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    settings.exports_dir.mkdir(parents=True, exist_ok=True)
    assert_schema_current(engine)
    with SessionLocal() as db:
        # Migration 0002 installs the starter templates; this only covers an
        # emptied table. A deleted template is never recreated (F-12.6).
        if not db.scalar(select(func.count()).select_from(SummaryTemplate)):
            db.add(SummaryTemplate(
                id=str(uuid.uuid4()),
                name=DEFAULT_TEMPLATE_NAME,
                description="Résumé exécutif, points clés, décisions, actions et détails.",
                prompt=DEFAULT_TEMPLATE,
                is_default=True,
            ))
            db.commit()
    yield


app = FastAPI(title="Sténo", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[x.strip() for x in settings.cors_origins.split(",") if x.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/ready")
def ready():
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
        ).ping()
    except Exception:
        logger.exception("Readiness check failed")
        raise HTTPException(503, "Dépendances indisponibles")
    return {"status": "ready"}


@app.get("/status")
async def status():
    """Per-service diagnostic report; unlike /ready it always answers 200."""
    return await system_status()


def _job_payload(job: ProcessingJob | None, snapshot: dict[str, QueueInfo]) -> dict | None:
    """JobOut fields plus the queue position and time estimate of an active job (n°13)."""
    if job is None:
        return None
    payload = JobOut.model_validate(job).model_dump()
    info = snapshot.get(job.id)
    if info is not None:
        payload.update(queue_position=info.position, estimated_seconds_remaining=info.seconds_remaining)
    return payload


def _latest_jobs(db, video_ids: list[str]) -> dict[str, ProcessingJob]:
    """Latest job of each video in one query (the library used to call /videos/{id} per row)."""
    if not video_ids:
        return {}
    latest = (
        select(ProcessingJob.video_id, func.max(ProcessingJob.created_at).label("created_at"))
        .where(ProcessingJob.video_id.in_(video_ids), ProcessingJob.kind != INDEX_KIND)
        .group_by(ProcessingJob.video_id)
        .subquery()
    )
    rows = db.scalars(select(ProcessingJob).where(ProcessingJob.kind != INDEX_KIND).join(
        latest, and_(ProcessingJob.video_id == latest.c.video_id, ProcessingJob.created_at == latest.c.created_at)
    ))
    return {job.video_id: job for job in rows}


def search_words(query: str | None) -> list[str]:
    """Words of a library search; anything else (quotes, operators) is dropped."""
    return re.findall(r"\w+", (query or "").casefold())[:SEARCH_MAX_WORDS]


def _search_condition(db, words: list[str]):
    """(filter, ranking) for PostgreSQL full-text search, (filter, None) elsewhere."""
    if db.get_bind().dialect.name == "postgresql":
        # Each word is a prefix: "réun" finds "réunion", accents ignored (migration 0005).
        tsquery = func.to_tsquery(
            literal_column("'simple'::regconfig"), func.f_unaccent(" & ".join(f"{word}:*" for word in words))
        )
        vector = literal_column("videos.search_vector")
        return vector.op("@@")(tsquery), func.ts_rank(vector, tsquery)
    # SQLite (unit tests): plain substring search.
    columns = [
        func.lower(Video.original_filename),
        func.lower(func.coalesce(Video.transcript_text, "")),
        func.lower(func.coalesce(Video.translated_text, "")),
    ]
    return and_(*[or_(*[column.contains(word, autoescape=True) for column in columns]) for word in words]), None


# Snippets around the matched words (n°17) are cut in the database: a 6-hour
# transcript never travels to the API. Only the best-ranked results get one.
SNIPPET_RESULTS = 30
SNIPPET_BEFORE = 100
SNIPPET_LENGTH = 320
SNIPPET_MAX_RANGES = 12
_CLOCK = re.compile(r"\[(\d{1,2}):(\d{2}):(\d{2})\]")


def fold_with_origin(value: str) -> tuple[str, list[int]]:
    """Lower-cased, accent-free text and, for each character of it, its index in `value`."""
    folded: list[str] = []
    origin: list[int] = []
    for index, char in enumerate(value):
        piece = "".join(c for c in unicodedata.normalize("NFD", char) if not unicodedata.combining(c)).casefold()
        folded.append(piece)
        origin.extend([index] * len(piece))
    return "".join(folded), origin


def build_snippet(fragment: str, fragment_start: int, match_offset: int, words: list[str], source: str) -> dict:
    """Readable extract of a raw transcript fragment: no timestamps, matched words located.

    `fragment_start` is the 1-based position of the fragment in the whole text and
    `match_offset` the position of the first hit inside it.
    """
    before = fragment[:max(0, match_offset)]
    clocks = list(_CLOCK.finditer(before)) or list(_CLOCK.finditer(fragment))
    start_seconds = None
    if clocks:
        pick = clocks[-1]
        start_seconds = float(int(pick.group(1)) * 3600 + int(pick.group(2)) * 60 + int(pick.group(3)))
    text = " ".join(_CLOCK.sub(" ", fragment.replace("\n", " … ")).split())
    lead = ""
    if fragment_start > 1 and " " in text:
        text, lead = text.split(" ", 1)[1], "… "  # the fragment starts inside a word
    tail = ""
    if len(fragment) >= SNIPPET_LENGTH and " " in text:
        text, tail = text.rsplit(" ", 1)[0], " …"
    folded, origin = fold_with_origin(text)
    ranges: list[list[int]] = []
    for word in words:
        needle, _ = fold_with_origin(word)
        for hit in re.finditer(r"(?<!\w)" + re.escape(needle) + r"\w*", folded):
            ranges.append([len(lead) + origin[hit.start()], len(lead) + origin[hit.end() - 1] + 1])
    ranges.sort()
    return {
        "text": f"{lead}{text}{tail}", "ranges": ranges[:SNIPPET_MAX_RANGES], "source": source, "start_seconds": start_seconds,
    }


def _snippets(db, video_ids: list[str], words: list[str]) -> dict[str, dict]:
    """First matched passage of each video, from its transcript, else from its translation."""
    if not video_ids or not words:
        return {}
    postgres = db.get_bind().dialect.name == "postgresql"

    def first_hit(column):
        # Position of the first word found, tried in the order typed. Accents are
        # ignored as in the search itself (PostgreSQL only: SQLite is the unit-test fallback).
        haystack = func.lower(func.f_unaccent(func.coalesce(column, ""))) if postgres else func.lower(func.coalesce(column, ""))
        locate = func.strpos if postgres else func.instr
        needles = [fold_with_origin(word)[0] if postgres else word for word in words]
        # The trailing 0 also keeps coalesce() at two arguments or more, as SQLite requires.
        return func.coalesce(*[func.nullif(locate(haystack, needle), 0) for needle in needles], 0)

    hits = select(
        Video.id.label("id"), first_hit(Video.transcript_text).label("pt"), first_hit(Video.translated_text).label("pl")
    ).where(Video.id.in_(video_ids)).subquery()

    def fragment(column, position):
        start = case((position > SNIPPET_BEFORE, position - SNIPPET_BEFORE), else_=1)
        return start, func.substr(column, start, SNIPPET_LENGTH)

    t_start, t_fragment = fragment(Video.transcript_text, hits.c.pt)
    l_start, l_fragment = fragment(Video.translated_text, hits.c.pl)
    rows = db.execute(
        select(hits.c.id, hits.c.pt, hits.c.pl, t_start, t_fragment, l_start, l_fragment).join(Video, Video.id == hits.c.id)
    ).all()
    snippets: dict[str, dict] = {}
    for video_id, pt, pl, ts, tf, ls, lf in rows:
        if pt:
            snippets[video_id] = build_snippet(tf or "", ts, pt - ts, words, "transcript")
        elif pl:
            snippets[video_id] = build_snippet(lf or "", ls, pl - ls, words, "translation")
    return snippets


@app.get("/videos", response_model=list[VideoListItem])
def list_videos(
    q: str | None = Query(None, max_length=200),
    status: str | None = Query(None, max_length=16),
    language: str | None = Query(None, max_length=32),
    tag: str | None = Query(None, max_length=TAG_MAX_CHARS),
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    limit: int = Query(500, ge=1, le=1000),
):
    """The whole library in one call (n°17): filters, full-text search, tags, latest job."""
    if status and status not in LIBRARY_STATUS_FILTERS:
        raise HTTPException(422, "Statut de filtre inconnu")
    with SessionLocal() as db:
        # Never load transcripts: a 6-hour one weighs ~400 kB.
        query = select(Video).options(
            load_only(
                Video.id, Video.original_filename, Video.duration_seconds, Video.size_bytes, Video.status,
                Video.detected_language, Video.target_language, Video.created_at,
            ),
            selectinload(Video.tags),
        )
        if status:
            query = query.where(Video.status.in_(LIBRARY_STATUS_FILTERS[status]))
        if language:
            query = query.where(Video.detected_language == language.strip().lower())
        if tag and tag.strip():
            query = query.where(Video.tags.any(func.lower(Tag.name) == tag.strip().lower()))
        if created_after:
            query = query.where(Video.created_at >= created_after)
        if created_before:
            query = query.where(Video.created_at < created_before)
        order = [desc(Video.created_at)]
        words = search_words(q)
        if words:
            condition, rank = _search_condition(db, words)
            query = query.where(condition)
            if rank is not None:
                order.insert(0, desc(rank))
        videos = list(db.scalars(query.order_by(*order).limit(limit)))
        snippets = _snippets(db, [video.id for video in videos[:SNIPPET_RESULTS]], words)
        jobs = _latest_jobs(db, [video.id for video in videos])
        snapshot = queue_snapshot(db)
        return [
            {
                "id": video.id,
                "original_filename": video.original_filename,
                "duration_seconds": video.duration_seconds,
                "size_bytes": video.size_bytes,
                "status": video.status,
                "detected_language": video.detected_language,
                "target_language": video.target_language,
                "created_at": video.created_at,
                "tags": [tag_row.name for tag_row in video.tags],
                "snippet": snippets.get(video.id),
                "job": _job_payload(jobs.get(video.id), snapshot),
            }
            for video in videos
        ]


@app.post("/videos", response_model=JobOut)
async def upload_video(
    file: UploadFile = File(...),
    target_language: str | None = Form(None),
    template_id: str | None = Form(None),
    custom_prompt: str | None = Form(None),
    summary_length: str | None = Form(None),
    source_language: str | None = Form(None),
    vocabulary: str | None = Form(None),
    use_global_glossary: bool = Form(True),
    diarize: bool = Form(False),
    num_speakers: int | None = Form(None),
):
    video_id = str(uuid.uuid4())
    original_filename = file.filename or "video.bin"
    suffix = Path(original_filename).suffix.lower()
    if suffix not in VIDEO_SUFFIXES | AUDIO_SUFFIXES:
        raise HTTPException(400, "Format de fichier non pris en charge")

    if len(original_filename) > 255:
        raise HTTPException(422, "Le nom du fichier ne peut pas dépasser 255 caractères")

    normalized_target_language = (target_language or "").strip() or None
    if normalized_target_language and len(normalized_target_language) > 32:
        raise HTTPException(422, "La langue cible ne peut pas dépasser 32 caractères")

    normalized_summary_length = (summary_length or "").strip() or DEFAULT_SUMMARY_LENGTH
    if normalized_summary_length not in SUMMARY_LENGTHS:
        raise HTTPException(422, "Longueur de résumé invalide")

    normalized_source_language = (source_language or "").strip().lower() or None
    if normalized_source_language and normalized_source_language not in SOURCE_LANGUAGES:
        raise HTTPException(422, "Langue source non prise en charge")

    normalized_custom_prompt = (custom_prompt or "").strip() or None
    if normalized_custom_prompt and len(normalized_custom_prompt) > CUSTOM_PROMPT_MAX_CHARS:
        raise HTTPException(
            422, f"Instructions supplémentaires : {len(normalized_custom_prompt)} caractères, limite {CUSTOM_PROMPT_MAX_CHARS}"
        )

    try:
        video_terms = parse_vocabulary(vocabulary)
    except TermsError as exc:
        raise HTTPException(422, str(exc)) from exc
    if num_speakers is not None and not 1 <= num_speakers <= MAX_SPEAKERS:
        raise HTTPException(422, f"Nombre d'intervenants : entre 1 et {MAX_SPEAKERS}")

    glossary_snapshot: list[str] = []
    if use_global_glossary:
        try:
            glossary_snapshot = _glossary_terms()
        except Exception:
            logger.exception("Unable to read the global glossary")
            raise HTTPException(503, "Service de données indisponible")

    normalized_template_id = (template_id or "").strip() or None
    if normalized_template_id and len(normalized_template_id) > 36:
        raise HTTPException(422, "Identifiant de template invalide")
    if normalized_template_id:
        try:
            with SessionLocal() as db:
                if db.get(SummaryTemplate, normalized_template_id) is None:
                    raise HTTPException(404, "Template introuvable")
        except HTTPException:
            raise
        except Exception:
            logger.exception("Unable to validate summary template %s", normalized_template_id)
            raise HTTPException(503, "Service de données indisponible")

    stored_name = f"{video_id}{suffix}"
    destination = settings.uploads_dir / stored_name
    bytes_written = 0
    try:
        with destination.open("wb") as out:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                bytes_written += len(chunk)
                if bytes_written > settings.max_upload_bytes:
                    raise HTTPException(
                        413,
                        f"Fichier trop volumineux (limite : {settings.max_upload_bytes} octets)",
                    )
                out.write(chunk)
    except HTTPException:
        destination.unlink(missing_ok=True)
        raise
    except Exception:
        destination.unlink(missing_ok=True)
        logger.exception("Unable to store uploaded file")
        raise HTTPException(500, "Impossible d'enregistrer le fichier envoyé")
    finally:
        await file.close()

    try:
        duration = ffprobe_duration(destination, timeout_seconds=settings.ffprobe_timeout_seconds)
    except Exception:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, "Fichier multimédia invalide ou illisible par ffprobe")

    if duration > settings.max_video_hours * 3600:
        destination.unlink(missing_ok=True)
        raise HTTPException(400, f"Durée maximale: {settings.max_video_hours:g} heures")

    job_id = str(uuid.uuid4())
    try:
        with SessionLocal() as db:
            video = Video(
                id=video_id,
                filename=stored_name,
                original_filename=original_filename,
                path=str(destination),
                duration_seconds=duration,
                size_bytes=destination.stat().st_size,
                status="QUEUED",
                target_language=normalized_target_language,
                detected_language=normalized_source_language,
                source_language_forced=normalized_source_language is not None,
                vocabulary=join_terms(video_terms),
                # Frozen at import: later glossary edits never change this video (F-11.13).
                glossary_snapshot=join_terms(glossary_snapshot),
                diarize=diarize,
                num_speakers=num_speakers if diarize else None,
            )
            job = ProcessingJob(
                id=job_id,
                video_id=video_id,
                stage="QUEUED",
                status="QUEUED",
                progress=0,
                template_id=normalized_template_id,
                custom_prompt=normalized_custom_prompt,
                summary_length=normalized_summary_length,
            )
            # ProcessingJob has a database foreign key to Video but no ORM
            # relationship.  Flush the parent explicitly so PostgreSQL cannot
            # receive the child INSERT first (SQLite's default test setup does
            # not enforce this ordering).
            db.add(video)
            db.flush()
            db.add(job)
            db.commit()

            try:
                queue = Queue(QUEUE_NAME, connection=Redis.from_url(settings.redis_url), default_timeout=21600)
                rq_job = queue.enqueue("app.worker.run_pipeline", job_id, job_timeout=21600, result_ttl=86400)
            except Exception as exc:
                logger.warning("Unable to enqueue processing job %s: %s", job_id, exc)
                db.delete(job)
                db.delete(video)
                db.commit()
                destination.unlink(missing_ok=True)
                raise HTTPException(503, "Service de traitement indisponible, réessayez ultérieurement") from exc

            job.rq_job_id = rq_job.id
            db.commit()
            db.refresh(job)
            return job
    except HTTPException:
        raise
    except Exception:
        destination.unlink(missing_ok=True)
        logger.exception("Unable to create upload records")
        raise HTTPException(500, "Impossible de créer le traitement vidéo")


@app.get("/videos/{video_id}", response_model=VideoDetail)
def get_video(video_id: str):
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        if not video:
            raise HTTPException(404, "Vidéo introuvable")
        _ = video.segments, video.summaries
        job = db.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.video_id == video_id, ProcessingJob.kind != INDEX_KIND)
            .order_by(desc(ProcessingJob.created_at))
        )
        template_names = dict(db.execute(select(SummaryTemplate.id, SummaryTemplate.name)).all())
        video_terms = split_stored_terms(video.vocabulary)
        glossary_terms = split_stored_terms(video.glossary_snapshot)
        vocabulary = effective_vocabulary(video_terms, glossary_terms)
        latest_summary = video.summaries[-1] if video.summaries else None
        edited_at = video.transcript_edited_at
        index = db.get(VideoIndex, video_id)
        return {
            "id": video.id,
            "original_filename": video.original_filename,
            "duration_seconds": video.duration_seconds,
            "size_bytes": video.size_bytes,
            "status": video.status,
            "detected_language": video.detected_language,
            "target_language": video.target_language,
            "created_at": video.created_at,
            "transcript_text": video.transcript_text,
            "translated_text": video.translated_text,
            "source_language_forced": video.source_language_forced,
            "vocabulary": video_terms,
            "glossary_snapshot": glossary_terms,
            "whisper_terms_count": len(whisper_terms(vocabulary)),
            "llm_terms_count": len(llm_terms(vocabulary)),
            "media_kind": "audio" if Path(video.path).suffix.lower() in AUDIO_SUFFIXES else "video",
            "source_available": Path(video.path).is_file(),
            "audio_available": _audio_path(video.id).is_file(),
            "chapters": video.chapters,
            "transcript_edited_at": edited_at,
            "translated_at": video.translated_at,
            "summary_outdated": bool(edited_at and latest_summary and _as_aware(latest_summary.created_at) < _as_aware(edited_at)),
            "translation_outdated": bool(
                edited_at and video.translated_text and (video.translated_at is None or _as_aware(video.translated_at) < _as_aware(edited_at))
            ),
            "tags": [tag_row.name for tag_row in video.tags],
            "chat_mode": _chat_mode(video, index),
            "diarize": video.diarize,
            "num_speakers": video.num_speakers,
            "diarization_error": video.diarization_error,
            "speakers": speakers_payload(video),
            "segments": video.segments,
            "summaries": [
                {
                    "id": summary.id,
                    "template_id": summary.template_id,
                    "template_name": template_names.get(summary.template_id),
                    "summary_length": summary.summary_length,
                    "language": summary.language,
                    "content_markdown": summary.content_markdown,
                    "model": summary.model,
                    "created_at": summary.created_at,
                    "edited_at": summary.edited_at,
                }
                for summary in video.summaries
            ],
            "job": _job_payload(job, queue_snapshot(db) if job and job.status in ACTIVE_JOB_STATUSES else {}),
        }


@app.delete("/videos/{video_id}")
def delete_video(video_id: str):
    with SessionLocal() as db:
        video = db.scalar(select(Video).where(Video.id == video_id).with_for_update())
        if not video:
            raise HTTPException(404, "Vidéo introuvable")
        job = db.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.video_id == video_id, ProcessingJob.kind != INDEX_KIND)
            .order_by(desc(ProcessingJob.created_at))
            .with_for_update()
        )
        running_index = list(db.scalars(select(ProcessingJob.rq_job_id).where(
            ProcessingJob.video_id == video_id, ProcessingJob.kind == INDEX_KIND, ProcessingJob.status == "RUNNING"
        )))
        if job and job.status == "RUNNING":
            raise HTTPException(409, "Annulez le traitement en cours avant de supprimer la vidéo")
        # A queued job is simply dropped: the worker ignores a job that no longer exists.
        queued_rq_job = job.rq_job_id if job and job.status == "QUEUED" else None
        Path(video.path).unlink(missing_ok=True)
        audio = settings.audio_dir / f"{video.id}.wav"
        audio.unlink(missing_ok=True)
        export_dir = settings.exports_dir / video.id
        if export_dir.exists():
            shutil.rmtree(export_dir)
        for job in db.scalars(select(ProcessingJob).where(ProcessingJob.video_id == video_id)):
            db.delete(job)
        db.delete(video)
        db.flush()
        _delete_orphan_tags(db)
        db.commit()
    _stop_rq_job(queued_rq_job, running=False)
    for rq_job_id in running_index:
        _stop_rq_job(rq_job_id, running=True)
    return {"deleted": True}


@app.post("/videos/{video_id}/retry", response_model=JobOut)
def retry_video(video_id: str):
    """Queue a failed video again without requiring its source to be uploaded twice."""
    with SessionLocal() as db:
        video = db.scalar(select(Video).where(Video.id == video_id).with_for_update())
        if not video:
            raise HTTPException(404, "Vidéo introuvable")
        if video.status not in ("FAILED", "CANCELLED"):
            raise HTTPException(409, "Seule une vidéo en erreur ou annulée peut être relancée")
        if not Path(video.path).is_file():
            raise HTTPException(409, "Fichier source introuvable")

        previous_job = db.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.video_id == video_id, ProcessingJob.kind != INDEX_KIND)
            .order_by(desc(ProcessingJob.created_at))
            .with_for_update()
        )
        if not previous_job or previous_job.status not in ("FAILED", "CANCELLED"):
            raise HTTPException(409, "La vidéo ne peut pas être relancée dans son état actuel")

        job = ProcessingJob(
            id=str(uuid.uuid4()),
            video_id=video.id,
            stage="QUEUED",
            status="QUEUED",
            progress=0,
            template_id=previous_job.template_id,
            custom_prompt=previous_job.custom_prompt,
            summary_length=previous_job.summary_length,
        )
        video.status = "QUEUED"
        db.add(job)
        db.commit()

        try:
            queue = Queue(QUEUE_NAME, connection=Redis.from_url(settings.redis_url), default_timeout=21600)
            rq_job = queue.enqueue("app.worker.run_pipeline", job.id, job_timeout=21600, result_ttl=86400)
        except Exception as exc:
            logger.warning("Unable to retry processing job %s: %s", job.id, exc)
            db.delete(job)
            video.status = "FAILED"
            db.commit()
            raise HTTPException(503, "Service de traitement indisponible, réessayez ultérieurement") from exc

        job.rq_job_id = rq_job.id
        db.commit()
        db.refresh(job)
        return job


@app.get("/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str):
    with SessionLocal() as db:
        job = db.get(ProcessingJob, job_id)
        if not job:
            raise HTTPException(404, "Job introuvable")
        return _job_payload(job, queue_snapshot(db) if job.status in ACTIVE_JOB_STATUSES else {})


def _stop_rq_job(rq_job_id: str | None, *, running: bool) -> None:
    """Best effort: the worker also stops at its next check of the job status."""
    if not rq_job_id:
        return
    try:
        connection = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        if running:
            # Kills the work-horse process group (ffmpeg included); Ollama stops
            # generating when the connection closes.
            send_stop_job_command(connection, rq_job_id)
        else:
            RQJob.fetch(rq_job_id, connection=connection).cancel()
    except Exception as exc:
        logger.warning("Unable to stop RQ job %s: %s", rq_job_id, exc)


@app.post("/jobs/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str):
    """Cancel a queued or running job (n°14).

    A FULL job leaves its video CANCELLED (it can be retried or deleted); a
    SUMMARY job leaves the video and its previous summary untouched.
    """
    with SessionLocal() as db:
        found = db.get(ProcessingJob, job_id)
        if not found:
            raise HTTPException(404, "Job introuvable")
        # Same lock order as the deletion and the retry: video, then job.
        video = db.scalar(select(Video).where(Video.id == found.video_id).with_for_update())
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
        if job.status not in ACTIVE_JOB_STATUSES:
            raise HTTPException(409, "Ce traitement est déjà terminé")
        running = job.status == "RUNNING"
        job.status = "CANCELLED"
        job.stage = "CANCELLED"
        job.finished_at = utcnow()
        if job.kind == "FULL" and video and video.status in ("QUEUED", "PROCESSING"):
            video.status = "CANCELLED"
        db.commit()
        db.refresh(job)
        rq_job_id = job.rq_job_id
        payload = _job_payload(job, {})
    _stop_rq_job(rq_job_id, running=running)
    return payload


@app.get("/videos/{video_id}/chat/messages", response_model=list[ChatMessageOut])
def list_chat_messages(video_id: str):
    with SessionLocal() as db:
        if not db.get(Video, video_id):
            raise HTTPException(404, "Vidéo introuvable")
        return list(db.scalars(
            select(VideoChatMessage)
            .where(VideoChatMessage.video_id == video_id)
            .order_by(VideoChatMessage.created_at)
        ))


def _chat_mode(video: Video, index: VideoIndex | None) -> str:
    """How the chat reads the video: "full" transcript, relevant "passages", or "partial" start only."""
    if not video.transcript_text:
        return "none"
    if len(video.transcript_text) <= CHAT_CONTEXT_LIMIT:
        return "full"
    if index and index.status == "READY" and index.model == settings.embedding_model:
        return "passages"
    return "partial"


def _passages_context(db, video: Video, question: str, history: list[tuple[str, str]]) -> str | None:
    """Relevant extracts of a long transcript (n°6); None when the index cannot be used."""
    try:
        query = embed_texts([retrieval_query(question, [content for role, content in history if role == "user"])])[0]
    except Exception:
        logger.warning("Question embedding failed for video %s; answering from the start of the transcript", video.id, exc_info=True)
        return None
    hits = merge_hits(search(db, [video.id], query, limit=CHAT_PASSAGES))
    if not hits:
        return None
    extracts = "\n\n---\n\n".join(hit.text for hit in sorted(hits, key=lambda hit: hit.start_seconds))
    return (
        f"EXTRAITS DE LA TRANSCRIPTION (les passages les plus proches de la question, dans l'ordre chronologique ; "
        f"la vidéo dure {timestamp(video.duration_seconds)} et le reste n'est pas montré. "
        "Si la réponse n'y figure pas, dis que tu ne la trouves pas dans les passages consultés) :\n"
        f"{extracts}"
    )


def _chat_inputs(video_id: str, question: str) -> tuple[list[tuple[str, str]], str, str, list[str]]:
    """History, context, fallback language and vocabulary of a question on a video."""
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        if not video:
            raise HTTPException(404, "Vidéo introuvable")
        if not video.transcript_text:
            raise HTTPException(409, "La transcription n'est pas encore disponible")
        history_rows = list(db.scalars(
            select(VideoChatMessage)
            .where(VideoChatMessage.video_id == video_id)
            .order_by(desc(VideoChatMessage.created_at))
            .limit(CHAT_HISTORY_LIMIT)
        ))
        history = [(message.role, message.content) for message in reversed(history_rows)]
        fallback_language = chat_fallback_language(
            [content for role, content in history if role == "user"],
            video.target_language,
            video.detected_language,
        )
        vocabulary = llm_terms(effective_vocabulary(
            split_stored_terms(video.vocabulary), split_stored_terms(video.glossary_snapshot)
        ))
        context_parts = []
        if video.summaries:
            # Video.summaries is ordered by created_at: the last one is the latest.
            context_parts.append(f"RÉSUMÉ :\n{video.summaries[-1].content_markdown}")
        mode = _chat_mode(video, db.get(VideoIndex, video_id))
        passages = _passages_context(db, video, question, history) if mode == "passages" else None
        if passages:
            context = "\n\n".join([*context_parts, passages])
        else:
            context_parts.append(f"TRANSCRIPTION :\n{video.transcript_text}")
            context = "\n\n".join(context_parts)
            if len(context) > CHAT_CONTEXT_LIMIT:
                context = context[:CHAT_CONTEXT_LIMIT] + "\n\n[Transcription tronquée pour respecter la fenêtre de contexte.]"
    return history, context, fallback_language, vocabulary


def _save_chat_exchange(video_id: str, question: str, answer: str, *, interrupted: bool = False) -> list[VideoChatMessage]:
    with SessionLocal() as db:
        # Persist both messages only once some answer exists: the history never
        # contains an orphaned question from an unavailable model.
        user_message = VideoChatMessage(id=str(uuid.uuid4()), video_id=video_id, role="user", content=question)
        assistant_message = VideoChatMessage(
            id=str(uuid.uuid4()), video_id=video_id, role="assistant", content=answer, interrupted=interrupted
        )
        db.add_all([user_message, assistant_message])
        db.commit()
        db.refresh(user_message)
        db.refresh(assistant_message)
        return [user_message, assistant_message]


@app.post("/videos/{video_id}/chat/messages", response_model=list[ChatMessageOut])
def ask_video_question(video_id: str, payload: ChatQuestion):
    history, context, fallback_language, vocabulary = _chat_inputs(video_id, payload.question)
    try:
        answer = answer_video_question(
            payload.question, context, history, fallback_language=fallback_language, vocabulary=vocabulary
        )
    except Exception as exc:
        logger.exception("Unable to answer question for video %s", video_id)
        raise HTTPException(503, ERROR_CHAT_UNAVAILABLE) from exc
    return _save_chat_exchange(video_id, payload.question, answer)


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/videos/{video_id}/chat/stream")
async def stream_video_answer(video_id: str, payload: ChatQuestion):
    """Answer as Server-Sent Events while the model writes it (n°16).

    Events: `delta` {text}, then `done` {messages: [question, answer]} once both
    are saved, or `error` {detail, saved}. A reader who leaves before the end
    stops the generation; what was written so far is kept, flagged
    `interrupted`, so a long answer is never lost.
    """
    history, context, fallback_language, vocabulary = await run_in_threadpool(_chat_inputs, video_id, payload.question)
    prompt = video_answer_prompt(
        payload.question, context, history, fallback_language=fallback_language, vocabulary=vocabulary
    )

    async def events():
        pieces: list[str] = []
        saved: list[VideoChatMessage] | None = None

        def keep(interrupted: bool) -> list[VideoChatMessage] | None:
            """Save the exchange once. A short synchronous write: it must also run when the reader is gone."""
            nonlocal saved
            answer = "".join(pieces).strip()
            if saved is None and answer:
                saved = _save_chat_exchange(video_id, payload.question, answer, interrupted=interrupted)
            return saved

        try:
            async for piece in stream_chat(prompt, temperature=CHAT_TEMPERATURE, max_output_tokens=CHAT_MAX_OUTPUT_TOKENS):
                pieces.append(piece)
                yield _sse("delta", {"text": piece})
            if not "".join(pieces).strip():
                raise RuntimeError("Empty answer")
            keep(False)
            yield _sse("done", {"messages": [ChatMessageOut.model_validate(m).model_dump(mode="json") for m in saved or []]})
        except Exception:
            logger.exception("Unable to stream an answer for video %s", video_id)
            yield _sse("error", {"detail": ERROR_CHAT_UNAVAILABLE, "saved": keep(True) is not None})
        finally:
            # The reader left (the task is cancelled): keep what was written.
            keep(True)

    # X-Accel-Buffering: a reverse proxy must pass each piece on at once.
    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.get("/jobs/{job_id}/events")
async def job_events(job_id: str):
    async def stream():
        last = None
        while True:
            with SessionLocal() as db:
                job = db.get(ProcessingJob, job_id)
                if not job:
                    yield "event: error\ndata: {\"error\":\"not_found\"}\n\n"
                    return
                payload = {
                    "id": job.id,
                    "video_id": job.video_id,
                    "kind": job.kind,
                    "stage": job.stage,
                    "status": job.status,
                    "progress": job.progress,
                    "error": job.error,
                }
                info = queue_snapshot(db).get(job.id) if job.status in ACTIVE_JOB_STATUSES else None
                if info is not None:
                    payload.update(queue_position=info.position, estimated_seconds_remaining=info.seconds_remaining)
            encoded = json.dumps(payload, ensure_ascii=False)
            if encoded != last:
                yield f"data: {encoded}\n\n"
                last = encoded
            if payload["status"] in TERMINAL_JOB_STATUSES:
                return
            await asyncio.sleep(1)
    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/templates", response_model=list[TemplateOut])
def templates():
    with SessionLocal() as db:
        return list(db.scalars(select(SummaryTemplate).order_by(desc(SummaryTemplate.is_default), SummaryTemplate.name)).all())


def _commit_template(db, failure_message: str) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if _is_template_name_conflict(exc):
            raise HTTPException(409, "Un template portant ce nom existe déjà") from exc
        logger.exception("Unable to persist summary template")
        raise HTTPException(500, failure_message) from exc
    except Exception as exc:
        db.rollback()
        logger.exception("Unable to persist summary template")
        raise HTTPException(500, failure_message) from exc


def _template_or_404(db, template_id: str, *, lock: bool = False) -> SummaryTemplate:
    query = select(SummaryTemplate).where(SummaryTemplate.id == template_id)
    template = db.scalar(query.with_for_update() if lock else query)
    if not template:
        raise HTTPException(404, "Template introuvable")
    return template


def _copy_name(name: str, existing: set[str]) -> str:
    """« <nom> (copie) », then « (copie 2) »…, within the 120-character limit."""
    number = 1
    while True:
        suffix = " (copie)" if number == 1 else f" (copie {number})"
        candidate = name[: 120 - len(suffix)] + suffix
        if candidate not in existing:
            return candidate
        number += 1


@app.post("/templates", response_model=TemplateOut)
def create_template(payload: TemplateCreate):
    tpl = SummaryTemplate(id=str(uuid.uuid4()), **payload.model_dump(), is_default=False)
    with SessionLocal() as db:
        db.add(tpl)
        _commit_template(db, "Impossible de créer le template")
        db.refresh(tpl)
        return tpl


@app.put("/templates/{template_id}", response_model=TemplateOut)
def update_template(template_id: str, payload: TemplateCreate):
    # A running job reads the template only at its final summary step (R-5).
    with SessionLocal() as db:
        tpl = _template_or_404(db, template_id, lock=True)
        tpl.name, tpl.description, tpl.prompt = payload.name, payload.description, payload.prompt
        _commit_template(db, "Impossible de modifier le template")
        db.refresh(tpl)
        return tpl


@app.delete("/templates/{template_id}")
def delete_template(template_id: str):
    with SessionLocal() as db:
        tpl = _template_or_404(db, template_id, lock=True)
        if tpl.is_default:
            raise HTTPException(409, "Définissez un autre template par défaut avant de supprimer celui-ci")
        in_use = db.scalar(
            select(func.count())
            .select_from(ProcessingJob)
            .where(ProcessingJob.template_id == template_id, ProcessingJob.status.in_(ACTIVE_JOB_STATUSES))
        )
        if in_use:
            raise HTTPException(409, "Ce template est utilisé par une analyse en cours ; réessayez après la fin du traitement")
        # Summaries keep their template_id and are shown as « Template supprimé » (R-6).
        db.delete(tpl)
        db.commit()
    return {"deleted": True}


@app.post("/templates/{template_id}/duplicate", response_model=TemplateOut)
def duplicate_template(template_id: str):
    with SessionLocal() as db:
        source = _template_or_404(db, template_id)
        existing = set(db.scalars(select(SummaryTemplate.name)))
        tpl = SummaryTemplate(
            id=str(uuid.uuid4()),
            name=_copy_name(source.name, existing),
            description=source.description,
            prompt=source.prompt,
            is_default=False,
        )
        db.add(tpl)
        _commit_template(db, "Impossible de dupliquer le template")
        db.refresh(tpl)
        return tpl


@app.post("/templates/{template_id}/default", response_model=TemplateOut)
def set_default_template(template_id: str):
    with SessionLocal() as db:
        # Locking every template serializes concurrent changes: never zero or two defaults (R-3).
        rows = list(db.scalars(select(SummaryTemplate).order_by(SummaryTemplate.id).with_for_update()))
        target = next((row for row in rows if row.id == template_id), None)
        if not target:
            raise HTTPException(404, "Template introuvable")
        for row in rows:
            if row.is_default and row is not target:
                row.is_default = False
        # Clear the previous default first: the partial unique index allows one row only.
        db.flush()
        target.is_default = True
        db.commit()
        db.refresh(target)
        return target


def _glossary_terms() -> list[str]:
    with SessionLocal() as db:
        return list(db.scalars(select(GlossaryTerm.term).order_by(GlossaryTerm.position)))


@app.get("/glossary", response_model=GlossaryOut)
def get_glossary():
    return {"terms": _glossary_terms()}


@app.put("/glossary", response_model=GlossaryOut)
def replace_glossary(payload: GlossaryIn):
    try:
        terms = parse_glossary(payload.terms)
    except TermsError as exc:
        raise HTTPException(422, str(exc)) from exc
    with SessionLocal() as db:
        # One transaction: the previous glossary stays intact if anything fails.
        db.execute(delete(GlossaryTerm))
        db.add_all([GlossaryTerm(term=term, position=index) for index, term in enumerate(terms)])
        db.commit()
    # Names of people and clients: log the size only.
    logger.info("Global glossary saved (%d terms)", len(terms))
    return {"terms": terms}


def _as_aware(value):
    """SQLite returns naive datetimes: compare everything as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _audio_path(video_id: str) -> Path:
    return settings.audio_dir / f"{video_id}.wav"


def _video_or_404(db, video_id: str, *, lock: bool = False) -> Video:
    query = select(Video).where(Video.id == video_id)
    video = db.scalar(query.with_for_update() if lock else query)
    if not video:
        raise HTTPException(404, "Vidéo introuvable")
    return video


def _refuse_if_busy(db, video_id: str) -> None:
    """Edits and regenerations wait for the running job: the worker writes the same rows."""
    job = db.scalar(
        select(ProcessingJob)
        .where(ProcessingJob.video_id == video_id, ProcessingJob.kind != INDEX_KIND)
        .order_by(desc(ProcessingJob.created_at))
    )
    if job and job.status in ACTIVE_JOB_STATUSES:
        raise HTTPException(409, ERROR_JOB_IN_PROGRESS)


def _media_response(path: Path) -> FileResponse:
    # FileResponse answers HTTP Range requests: the player can seek anywhere.
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=3600"})


@app.get("/videos/{video_id}/media")
def video_media(video_id: str):
    with SessionLocal() as db:
        path = Path(_video_or_404(db, video_id).path)
    if not path.is_file():
        raise HTTPException(404, "Fichier source introuvable")
    return _media_response(path)


@app.get("/videos/{video_id}/audio")
def video_audio(video_id: str):
    """Extracted mono WAV: fallback when the browser cannot decode the source (MKV, AVI…)."""
    with SessionLocal() as db:
        _video_or_404(db, video_id)
    path = _audio_path(video_id)
    if not path.is_file():
        raise HTTPException(404, "Piste audio non disponible")
    return _media_response(path)


@app.post("/videos/{video_id}/summaries", response_model=JobOut)
def regenerate_summary(video_id: str, payload: RegenerateIn):
    """New summary of the existing transcript: no audio extraction nor transcription (n°9)."""
    if payload.summary_length not in SUMMARY_LENGTHS:
        raise HTTPException(422, "Longueur de résumé invalide")
    with SessionLocal() as db:
        video = _video_or_404(db, video_id, lock=True)
        if video.status != "COMPLETED" or not video.transcript_text:
            raise HTTPException(409, "Le résumé ne peut être régénéré qu'une fois la vidéo traitée")
        _refuse_if_busy(db, video_id)
        template_id = payload.template_id or None
        if template_id and db.get(SummaryTemplate, template_id) is None:
            raise HTTPException(404, "Template introuvable")
        job = ProcessingJob(
            id=str(uuid.uuid4()),
            video_id=video_id,
            kind="SUMMARY",
            stage="QUEUED",
            status="QUEUED",
            progress=0,
            template_id=template_id,
            custom_prompt=payload.custom_prompt or None,
            summary_length=payload.summary_length,
        )
        db.add(job)
        db.commit()
        try:
            queue = Queue(QUEUE_NAME, connection=Redis.from_url(settings.redis_url), default_timeout=21600)
            rq_job = queue.enqueue("app.worker.run_summary", job.id, job_timeout=21600, result_ttl=86400)
        except Exception as exc:
            logger.warning("Unable to enqueue summary job %s: %s", job.id, exc)
            db.delete(job)
            db.commit()
            raise HTTPException(503, "Service de traitement indisponible, réessayez ultérieurement") from exc
        job.rq_job_id = rq_job.id
        db.commit()
        db.refresh(job)
        return job


@app.put("/videos/{video_id}/summaries/{summary_id}", response_model=SummaryOut)
def edit_summary(video_id: str, summary_id: str, payload: SummaryEditIn):
    with SessionLocal() as db:
        video = _video_or_404(db, video_id, lock=True)
        _refuse_if_busy(db, video_id)
        summary = db.get(Summary, summary_id)
        if not summary or summary.video_id != video_id:
            raise HTTPException(404, "Résumé introuvable")
        summary.content_markdown = payload.content_markdown
        summary.edited_at = utcnow()
        db.commit()
        if video.status == "COMPLETED":
            write_exports(db, video_id)
        db.refresh(summary)
        return summary


def _save_transcript_edit(db, video: Video) -> None:
    """Rebuild the plain transcript from its segments and refresh the exports."""
    db.flush()
    db.refresh(video)
    video.transcript_text = build_transcript(video)
    video.transcript_edited_at = utcnow()
    index = db.get(VideoIndex, video.id)
    if index:
        index.status = "STALE"
    db.commit()
    if video.status == "COMPLETED":
        write_exports(db, video.id)
        # The chat answers from the passages: they must follow the correction.
        enqueue_index_job(video.id)


@app.patch("/videos/{video_id}/segments/{segment_id}", response_model=SegmentOut)
def edit_segment(video_id: str, segment_id: int, payload: SegmentEditIn):
    with SessionLocal() as db:
        video = _video_or_404(db, video_id, lock=True)
        _refuse_if_busy(db, video_id)
        segment = db.get(TranscriptSegment, segment_id)
        if not segment or segment.video_id != video_id:
            raise HTTPException(404, "Segment introuvable")
        changed = False
        if payload.text is not None and segment.text != payload.text:
            segment.text = payload.text
            changed = True
        if "speaker_id" in payload.model_fields_set and segment.speaker_id != payload.speaker_id:
            if payload.speaker_id is not None and not any(s.id == payload.speaker_id for s in video.speakers):
                raise HTTPException(404, "Intervenant introuvable")
            segment.speaker_id = payload.speaker_id
            changed = True
        if changed:
            _save_transcript_edit(db, video)
        db.refresh(segment)
        return segment


@app.post("/videos/{video_id}/transcript/replace", response_model=ReplaceOut)
def replace_in_transcript(video_id: str, payload: ReplaceIn):
    """Fix a recurring misrecognition (a name, an acronym) in every segment at once."""
    pattern = re.escape(payload.find)
    if payload.whole_word:
        pattern = rf"(?<!\w){pattern}(?!\w)"
    regex = re.compile(pattern, 0 if payload.match_case else re.IGNORECASE)
    with SessionLocal() as db:
        video = _video_or_404(db, video_id, lock=True)
        _refuse_if_busy(db, video_id)
        replaced = changed = 0
        for segment in video.segments:
            # A function replacement keeps "\1" or "\g<0>" typed by the user literal.
            text, count = regex.subn(lambda _: payload.replace, segment.text)
            if count:
                segment.text = text
                replaced += count
                changed += 1
        if replaced:
            _save_transcript_edit(db, video)
        return {"replaced": replaced, "segments": changed}


def _speaker_or_404(video: Video, speaker_id: int) -> Speaker:
    speaker = next((s for s in video.speakers if s.id == speaker_id), None)
    if speaker is None:
        raise HTTPException(404, "Intervenant introuvable")
    return speaker


@app.post("/videos/{video_id}/speakers/detect", response_model=JobOut)
def detect_speakers(video_id: str, payload: DiarizeIn):
    """Identify the speakers of a processed video (n°8), e.g. one imported before this option."""
    with SessionLocal() as db:
        video = _video_or_404(db, video_id, lock=True)
        if video.status != "COMPLETED" or not video.transcript_text:
            raise HTTPException(409, "Les intervenants ne peuvent être identifiés qu'une fois la vidéo traitée")
        _refuse_if_busy(db, video_id)
        video.diarize = True
        video.num_speakers = payload.num_speakers
        job = ProcessingJob(id=str(uuid.uuid4()), video_id=video_id, kind="DIARIZE", stage="QUEUED", status="QUEUED", progress=0)
        db.add(job)
        db.commit()
        try:
            queue = Queue(QUEUE_NAME, connection=Redis.from_url(settings.redis_url), default_timeout=21600)
            rq_job = queue.enqueue("app.worker.run_diarize", job.id, job_timeout=21600, result_ttl=86400)
        except Exception as exc:
            logger.warning("Unable to enqueue diarization job %s: %s", job.id, exc)
            db.delete(job)
            db.commit()
            raise HTTPException(503, "Service de traitement indisponible, réessayez ultérieurement") from exc
        job.rq_job_id = rq_job.id
        db.commit()
        db.refresh(job)
        return job


@app.put("/videos/{video_id}/speakers/{speaker_id}")
def rename_speaker(video_id: str, speaker_id: int, payload: SpeakerRenameIn):
    """A name for « Intervenant 2 »: the transcript, exports and index follow."""
    with SessionLocal() as db:
        video = _video_or_404(db, video_id, lock=True)
        _refuse_if_busy(db, video_id)
        speaker = _speaker_or_404(video, speaker_id)
        name = " ".join((payload.name or "").split()) or None
        if name and any(s.id != speaker.id and s.label.casefold() == name.casefold() for s in video.speakers):
            raise HTTPException(409, "Un autre intervenant porte déjà ce nom ; fusionnez-les plutôt")
        if speaker.name != name:
            old_label = speaker.label
            speaker.name = name
            video.translated_text = relabel_translation(video.translated_text, old_label, speaker.label)
            _save_transcript_edit(db, video)
        return {"speakers": speakers_payload(video)}


@app.post("/videos/{video_id}/speakers/{speaker_id}/merge")
def merge_speaker(video_id: str, speaker_id: int, payload: SpeakerMergeIn):
    """One person found twice by the diarization: their lines join the other speaker."""
    with SessionLocal() as db:
        video = _video_or_404(db, video_id, lock=True)
        _refuse_if_busy(db, video_id)
        source = _speaker_or_404(video, speaker_id)
        target = _speaker_or_404(video, payload.into)
        if source.id == target.id:
            raise HTTPException(422, "Choisissez un autre intervenant")
        for segment in video.segments:
            if segment.speaker_id == source.id:
                segment.speaker_id = target.id
        video.translated_text = relabel_translation(video.translated_text, source.label, target.label)
        video.speakers.remove(source)
        _save_transcript_edit(db, video)
        return {"speakers": speakers_payload(video)}


@app.get("/videos/{video_id}/exports/{name}")
def export(video_id: str, name: str, transcript: str = Query("original", max_length=16)):
    if name in REPORT_TYPES:
        return _report(video_id, name, transcript)
    if name not in EXPORT_NAMES:
        raise HTTPException(404, "Export inconnu")
    path = settings.exports_dir / video_id / name
    if not path.exists():
        raise HTTPException(404, "Export non disponible")
    return FileResponse(path, filename=name)


# --- Tags (n°17) ---------------------------------------------------------------------

def parse_tags(values: list[str]) -> list[str]:
    """Trimmed, single-spaced, unique regardless of case; the first spelling wins."""
    tags: list[str] = []
    seen: set[str] = set()
    for value in values:
        name = " ".join(value.split())
        if not name:
            continue
        if len(name) > TAG_MAX_CHARS:
            raise HTTPException(422, f"Un tag ne peut pas dépasser {TAG_MAX_CHARS} caractères")
        if name.casefold() not in seen:
            seen.add(name.casefold())
            tags.append(name)
    if len(tags) > TAGS_PER_VIDEO:
        raise HTTPException(422, f"{TAGS_PER_VIDEO} tags au plus par vidéo")
    return tags


def _delete_orphan_tags(db) -> None:
    db.execute(delete(Tag).where(~exists().where(video_tags.c.tag_id == Tag.id)))


@app.get("/tags", response_model=list[TagCount])
def list_tags():
    with SessionLocal() as db:
        rows = db.execute(
            select(Tag.name, func.count(video_tags.c.video_id))
            .join(video_tags, video_tags.c.tag_id == Tag.id)
            .group_by(Tag.id, Tag.name)
            .order_by(func.lower(Tag.name))
        ).all()
        return [{"name": name, "count": count} for name, count in rows]


@app.put("/videos/{video_id}/tags", response_model=TagsOut)
def set_video_tags(video_id: str, payload: TagsIn):
    names = parse_tags(payload.tags)
    # Two videos may create the same new tag at once: the unique index rejects
    # the second insert, which then finds the tag on its retry.
    for attempt in range(2):
        with SessionLocal() as db:
            video = _video_or_404(db, video_id, lock=True)
            existing = {
                tag.name.casefold(): tag
                for tag in db.scalars(select(Tag).where(func.lower(Tag.name).in_([name.lower() for name in names])))
            } if names else {}
            video.tags = [existing.get(name.casefold()) or Tag(name=name) for name in names]
            try:
                db.flush()
                _delete_orphan_tags(db)
                db.commit()
            except IntegrityError:
                db.rollback()
                if attempt:
                    raise HTTPException(409, "Tags modifiés en même temps ailleurs ; réessayez")
                continue
            return {"tags": [tag.name for tag in sorted(video.tags, key=lambda tag: tag.name.casefold())]}


# --- Questions on several videos (n°19) -------------------------------------------------

LIBRARY_HISTORY_MESSAGES = 12
LIBRARY_HISTORY_ANSWER_CHARS = 2000
CONVERSATIONS_LIMIT = 100


def _conversation_or_404(db, conversation_id: str) -> LibraryConversation:
    conversation = db.get(LibraryConversation, conversation_id)
    if conversation is None:
        raise HTTPException(404, "Conversation introuvable")
    return conversation


def _conversation_out(conversation: LibraryConversation) -> dict:
    return {
        "id": conversation.id,
        "title": conversation.title,
        "scope": conversation.scope,
        "video_count": len(json.loads(conversation.video_ids)),
        "created_at": conversation.created_at,
        "updated_at": conversation.updated_at,
    }


def _library_prepare(payload: LibraryQuestion) -> dict:
    """Conversation, history and numbered passages closest to the question, among the indexed videos in scope."""
    with SessionLocal() as db:
        conversation = _conversation_or_404(db, payload.conversation_id) if payload.conversation_id else None
        if conversation is not None:
            video_ids = json.loads(conversation.video_ids)
            history = [
                (message.role, message.content[:LIBRARY_HISTORY_ANSWER_CHARS])
                for message in conversation.messages[-LIBRARY_HISTORY_MESSAGES:]
            ]
        else:
            video_ids = payload.video_ids
            history = []
            if not video_ids:
                raise HTTPException(422, "Choisissez au moins une vidéo")
        video_ids = list(dict.fromkeys(video_ids))[:LIBRARY_MAX_VIDEOS]
        titles = dict(db.execute(
            select(Video.id, Video.original_filename).where(Video.id.in_(video_ids), Video.status == "COMPLETED")
        ).all())
        ready = sorted(ready_video_ids(db, list(titles)))
        if not ready:
            raise HTTPException(409, "Aucune de ces vidéos n'est encore indexée pour les questions ; réessayez dans quelques minutes")
        previous = [content for role, content in history if role == "user"]
        try:
            query = embed_texts([retrieval_query(payload.question, previous)])[0]
        except Exception as exc:
            logger.exception("Question embedding failed")
            raise HTTPException(503, "Recherche indisponible : le modèle d'embeddings ne répond pas") from exc
        hits: list[Hit] = merge_hits(search(
            db, ready, query, limit=LIBRARY_PASSAGES, per_video=LIBRARY_PASSAGES_PER_VIDEO
        ))
        is_new = conversation is None
        if conversation is None:
            now = utcnow()
            conversation = LibraryConversation(
                id=str(uuid.uuid4()),
                title=" ".join(payload.question.split())[:120],
                scope=(payload.scope or "").strip() or None,
                video_ids=json.dumps(video_ids),
                created_at=now,
                updated_at=now,
            )
            db.add(conversation)
            db.commit()
        conversation_id = conversation.id
    # Most relevant first: the model reads the list in this order.
    hits.sort(key=lambda hit: hit.distance)
    sources = [
        {
            "n": number,
            "video_id": hit.video_id,
            "title": titles.get(hit.video_id, ""),
            "start_seconds": hit.start_seconds,
            "end_seconds": hit.end_seconds,
        }
        for number, hit in enumerate(hits, 1)
    ]
    text = "\n\n".join(
        f"[{source['n']}] Vidéo « {source['title']} », de {timestamp(hit.start_seconds)} à {timestamp(hit.end_seconds)} :\n{hit.text}"
        for source, hit in zip(sources, hits)
    )
    return {
        "conversation_id": conversation_id, "is_new": is_new, "history": history, "sources": sources, "text": text,
        "searched": len(ready), "skipped": len(titles) - len(ready),
    }


def _save_library_exchange(conversation_id: str, question: str, answer: str, sources: list[dict], *, interrupted: bool) -> bool:
    with SessionLocal() as db:
        conversation = db.get(LibraryConversation, conversation_id)
        if conversation is None:  # deleted while the answer was written
            return False
        now = utcnow()
        db.add(LibraryMessage(id=str(uuid.uuid4()), conversation_id=conversation_id, role="user", content=question, created_at=now))
        db.add(LibraryMessage(
            id=str(uuid.uuid4()), conversation_id=conversation_id, role="assistant", content=answer,
            sources=json.dumps(sources, ensure_ascii=False), interrupted=interrupted, created_at=now + timedelta(milliseconds=1),
        ))
        conversation.updated_at = now
        db.commit()
    return True


def _discard_empty_conversation(conversation_id: str) -> None:
    with SessionLocal() as db:
        conversation = db.get(LibraryConversation, conversation_id)
        if conversation is not None and not conversation.messages:
            db.delete(conversation)
            db.commit()


@app.post("/library/chat/stream")
async def stream_library_answer(payload: LibraryQuestion):
    """Answer from passages of several videos, as Server-Sent Events (n°19).

    Events: `sources` {conversation_id, sources, searched, skipped}, then
    `delta` {text}, then `done` {answer, conversation_id} or `error` {detail,
    saved}. The exchange is kept in a conversation; what was written before the
    reader left, or before the model failed, is kept too, flagged `interrupted`.
    """
    prepared = await run_in_threadpool(_library_prepare, payload)
    conversation_id, sources = prepared["conversation_id"], prepared["sources"]
    prompt = library_answer_prompt(payload.question, prepared["text"], prepared["history"])

    async def events():
        pieces: list[str] = []
        saved = False

        def keep(interrupted: bool) -> bool:
            nonlocal saved
            answer = "".join(pieces).strip()
            if not saved and answer:
                saved = _save_library_exchange(conversation_id, payload.question, answer, sources, interrupted=interrupted)
            return saved

        try:
            yield _sse("sources", {
                "conversation_id": conversation_id, "sources": sources,
                "searched": prepared["searched"], "skipped": prepared["skipped"],
            })
            async for piece in stream_chat(prompt, temperature=CHAT_TEMPERATURE, max_output_tokens=CHAT_MAX_OUTPUT_TOKENS * 2):
                pieces.append(piece)
                yield _sse("delta", {"text": piece})
            answer = "".join(pieces).strip()
            if not answer:
                raise RuntimeError("Empty answer")
            keep(False)
            yield _sse("done", {"answer": answer, "conversation_id": conversation_id})
        except Exception:
            logger.exception("Unable to stream a library answer")
            yield _sse("error", {"detail": ERROR_CHAT_UNAVAILABLE, "saved": keep(True)})
        finally:
            keep(True)
            if prepared["is_new"] and not saved:
                _discard_empty_conversation(conversation_id)

    return StreamingResponse(
        events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    )


@app.get("/library/conversations", response_model=list[LibraryConversationOut])
def list_conversations():
    with SessionLocal() as db:
        rows = db.scalars(select(LibraryConversation).order_by(desc(LibraryConversation.updated_at)).limit(CONVERSATIONS_LIMIT))
        return [_conversation_out(conversation) for conversation in rows]


@app.get("/library/conversations/{conversation_id}", response_model=LibraryConversationDetail)
def get_conversation(conversation_id: str):
    with SessionLocal() as db:
        conversation = _conversation_or_404(db, conversation_id)
        return {
            **_conversation_out(conversation),
            "video_ids": json.loads(conversation.video_ids),
            "messages": [
                {
                    "id": message.id, "role": message.role, "content": message.content,
                    "sources": json.loads(message.sources) if message.sources else [],
                    "interrupted": message.interrupted, "created_at": message.created_at,
                }
                for message in conversation.messages
            ],
        }


@app.delete("/library/conversations/{conversation_id}")
def delete_conversation(conversation_id: str):
    with SessionLocal() as db:
        db.delete(_conversation_or_404(db, conversation_id))
        db.commit()
    return {"deleted": True}


def _report(video_id: str, name: str, transcript: str = "original") -> Response:
    """Meeting report (n°20), rendered from the current state: never stale.

    `transcript` picks the annex: the original transcript, its translation, or none.
    """
    if transcript not in TRANSCRIPT_MODES:
        raise HTTPException(422, "Annexe inconnue : original, translation ou none")
    media_type, render = REPORT_TYPES[name]
    with SessionLocal() as db:
        video = _video_or_404(db, video_id)
        if video.status != "COMPLETED":
            raise HTTPException(404, "Export non disponible")
        if transcript == "translation" and not video.translated_text:
            raise HTTPException(404, "Aucune traduction disponible")
        report = build_report(db, video, transcript=transcript)
    content = render(report)
    # ASCII fallback for old clients, the real name in filename* (RFC 5987).
    stem = re.sub(r"[^\w\- ]+", "_", report.title)[:80].strip() or "compte-rendu"
    extension = name.rsplit(".", 1)[1]
    ascii_name = stem.encode("ascii", "ignore").decode() or "compte-rendu"
    kind = "compte-rendu (traduit)" if transcript == "translation" else "compte-rendu"
    disposition = (
        f'attachment; filename="{ascii_name} - {kind}.{extension}"; '
        f"filename*=UTF-8''{quote(f'{stem} - {kind}.{extension}')}"
    )
    return Response(content, media_type=media_type, headers={"Content-Disposition": disposition})
