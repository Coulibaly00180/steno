import asyncio
import json
import mimetypes
import re
import shutil
import subprocess
import unicodedata

import httpx
import uuid
import logging
from urllib.parse import quote, unquote, urlsplit
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from http.cookies import CookieError, SimpleCookie
from redis import Redis
from rq import Queue
from rq.command import send_stop_job_command
from rq.job import Job as RQJob
from sqlalchemy import and_, case, delete, desc, exists, func, literal_column, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import load_only, selectinload

from .analysis_options import (
    DEFAULT_SUMMARY_LENGTH,
    GLOSSARY_MAX_TERMS,
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
from . import ai_models, app_settings, auth, backups, entities, ollama_catalog, portable, quality, url_import, watch_folder
from .config import QUEUE_NAME, settings
from .learning import corrections_between, record_corrections, replacement_pair
from .learning import dismiss as dismiss_suggestion
from .learning import suggestions as glossary_suggestions
from .storage import (
    AUDIO_SUFFIXES,
    COMPRESSED_AUDIO_SUFFIXES,
    SOURCE_POLICIES,
    VIDEO_SUFFIXES,
    StorageError,
    delete_media,
    delete_work_audio,
    disk_summary,
    video_usage,
)
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
    Entity,
    EntityMention,
    GlossaryTerm,
    LibraryConversation,
    LibraryMessage,
    LiveSegment,
    ProcessingJob,
    Recording,
    SavedSearch,
    Summary,
    SummaryTemplate,
    Tag,
    TranscriptSegment,
    Video,
    Speaker,
    VideoChatMessage,
    VideoEntityState,
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
    BackupSettings,
    BenchmarkIn,
    ConversationRenameIn,
    EntityMergeIn,
    EntityUpdateIn,
    FeedbackIn,
    ModelPullIn,
    ModelSettings,
    SavedSearchIn,
    GlossarySuggestionOut,
    GlossaryTermIn,
    ImportOptionsIn,
    RecordingCreate,
    RecordingOut,
    UrlImportIn,
    UrlImportSettings,
    UrlPreviewIn,
    StorageActionIn,
    WatchFolderSettings,
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
from .status import BENCHMARK_KEY, BENCHMARK_QUEUE, GPU_KEY, system_status
from .utils import ffprobe_duration, timestamp
from .worker import DEFAULT_TEMPLATE, enqueue_entities_job, enqueue_index_job, mark_entities_stale

logger = logging.getLogger(__name__)
UPLOAD_CHUNK_SIZE = 1024 * 1024
CHAT_HISTORY_LIMIT = 12
CHAT_CONTEXT_LIMIT = 30000
# Beyond CHAT_CONTEXT_LIMIT, the chat reads the passages closest to the question (n°6).
CHAT_PASSAGES = 12
LIBRARY_PASSAGES = 12
LIBRARY_PASSAGES_PER_VIDEO = 4
LIBRARY_MAX_VIDEOS = 500
# The library page by page: a list of thousands of rows was cut at 500 (measured with 3 000 videos).
LIBRARY_PAGE_SIZE = 100
LIBRARY_PAGE_MAX = 500
# Background jobs (semantic index, entities) and clips: never "the video's job" in the interface.
BACKGROUND_KINDS = ("INDEX", "ENTITIES", "CLIP")
CUSTOM_PROMPT_MAX_CHARS = 2000
DEFAULT_TEMPLATE_NAME = "Compte-rendu de réunion"
TERMINAL_JOB_STATUSES = ("COMPLETED", "FAILED", "CANCELLED")
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
    settings.inbox_dir.mkdir(parents=True, exist_ok=True)
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
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


class AccessGuard:
    """Password check of the requests from the network (n°15, rules in app.auth).

    Plain ASGI rather than BaseHTTPMiddleware: uploads, SSE streams and media
    downloads pass through untouched.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        if scope["type"] != "http" or path in auth.PUBLIC_PATHS or path.startswith("/auth/"):
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1") for key, value in scope.get("headers", [])}
        remote = headers.get(auth.REMOTE_HEADER) == "1"
        config = auth.cached()
        if config is None:
            try:
                config = await run_in_threadpool(auth.load)
            except Exception:
                logger.warning("Unable to read the access settings", exc_info=True)
                if not remote:
                    await self.app(scope, receive, send)
                    return
                await JSONResponse({"detail": "Service indisponible"}, status_code=503)(scope, receive, send)
                return
        cookies = SimpleCookie()
        try:
            cookies.load(headers.get("cookie", ""))
        except CookieError:
            pass
        token = cookies[auth.COOKIE].value if auth.COOKIE in cookies else None
        decision = auth.decide(path, remote=remote, token=token, config=config)
        if decision.allowed:
            await self.app(scope, receive, send)
            return
        await JSONResponse({"detail": decision.detail}, status_code=decision.status)(scope, receive, send)


app = FastAPI(title="Sténo", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[x.strip() for x in settings.cors_origins.split(",") if x.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(AccessGuard)


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
        .where(ProcessingJob.video_id.in_(video_ids), ProcessingJob.kind.not_in(BACKGROUND_KINDS))
        .group_by(ProcessingJob.video_id)
        .subquery()
    )
    rows = db.scalars(select(ProcessingJob).where(ProcessingJob.kind.not_in(BACKGROUND_KINDS)).join(
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
        if not postgres:
            haystack = func.lower(func.coalesce(column, ""))
            # The trailing 0 also keeps coalesce() at two arguments or more, as SQLite requires.
            return func.coalesce(*[func.nullif(func.instr(haystack, word), 0) for word in words], 0)
        # The word as typed first, then without accents: removing the accents of 30
        # whole transcripts took 200 ms of a search over 3 000 videos, and is only
        # needed when the transcript spells the word differently (COALESCE stops at
        # the first position found).
        plain = func.lower(func.coalesce(column, ""))
        folded = func.lower(func.f_unaccent(func.coalesce(column, "")))
        positions = []
        for word in words:
            positions += [func.nullif(func.strpos(plain, word.lower()), 0), func.nullif(func.strpos(folded, fold_with_origin(word)[0]), 0)]
        return func.coalesce(*positions, 0)

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


# Hybrid search (n°17): videos whose passages are close to the query in meaning
# join those containing its words. Beyond this cosine distance (bge-m3), a
# passage is not about the query. Measured on a laptop review (2026-09-24):
# queries on its subject 0.37-0.47, « prix et promotions » 0.52, off-topic
# (« réunion budget », « météo », « football ») 0.56-0.72.
SEMANTIC_MAX_DISTANCE = 0.52
SEMANTIC_VIDEOS = 30
# Reciprocal rank fusion: a video ranked well by either search comes first.
RRF_K = 60


def _semantic_matches(db, filters: list, q: str) -> dict[str, Hit] | None:
    """Closest passage of each video in the filtered library; None when the embeddings are unavailable."""
    candidates = list(db.scalars(select(Video.id).where(Video.status == "COMPLETED", *filters)))
    ready = sorted(ready_video_ids(db, candidates))
    if not ready:
        return {}
    try:
        query = embed_texts([q.strip()])[0]
    except Exception:
        logger.warning("Query embedding failed; library search by words only", exc_info=True)
        return None
    hits = search(db, ready, query, limit=SEMANTIC_VIDEOS, per_video=1)
    return {hit.video_id: hit for hit in hits if hit.distance <= SEMANTIC_MAX_DISTANCE}


def semantic_snippet(hit: Hit) -> dict:
    """The passage found by meaning, as a snippet: no word to highlight."""
    text = " ".join(_CLOCK.sub(" ", hit.text.replace("\n", " … ")).split())
    if len(text) > SNIPPET_LENGTH:
        text = text[:SNIPPET_LENGTH].rsplit(" ", 1)[0] + " …"
    return {"text": text, "ranges": [], "source": "meaning", "start_seconds": hit.start_seconds}


@app.get("/videos", response_model=list[VideoListItem])
def list_videos(
    response: Response,
    q: str | None = Query(None, max_length=200),
    status: str | None = Query(None, max_length=16),
    language: str | None = Query(None, max_length=32),
    tag: str | None = Query(None, max_length=TAG_MAX_CHARS),
    entity: int | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    mode: str = Query("hybrid", pattern="^(hybrid|exact)$"),
    limit: int = Query(LIBRARY_PAGE_SIZE, ge=1, le=LIBRARY_PAGE_MAX),
    offset: int = Query(0, ge=0),
):
    """One page of the library (n°17): filters, hybrid search, tags, latest job.

    The whole ordering is computed on ids only (cheap), then the page's rows are
    loaded: a library of thousands of videos is browsed page by page, none is
    out of reach. `X-Total-Count` gives how many videos match.
    `mode=exact`: words only. The `X-Search-Mode` header says which search ran
    (hybrid falls back to words when the embedding model does not answer).
    """
    if status and status not in LIBRARY_STATUS_FILTERS:
        raise HTTPException(422, "Statut de filtre inconnu")
    with SessionLocal() as db:
        filters = []
        if status:
            filters.append(Video.status.in_(LIBRARY_STATUS_FILTERS[status]))
        if language:
            filters.append(Video.detected_language == language.strip().lower())
        if tag and tag.strip():
            filters.append(Video.tags.any(func.lower(Tag.name) == tag.strip().lower()))
        if entity is not None:
            filters.append(Video.id.in_(select(EntityMention.video_id).where(EntityMention.entity_id == entity)))
        if created_after:
            filters.append(Video.created_at >= created_after)
        if created_before:
            filters.append(Video.created_at < created_before)
        # Never load transcripts: a 6-hour one weighs ~400 kB.
        columns = load_only(
            Video.id, Video.original_filename, Video.duration_seconds, Video.size_bytes, Video.status,
            Video.detected_language, Video.target_language, Video.created_at,
        )
        id_query = select(Video.id).where(*filters)
        order = [desc(Video.created_at), Video.id]
        words = search_words(q)
        semantic: dict[str, Hit] = {}
        search_mode = "exact"
        if words:
            if mode == "hybrid":
                found = _semantic_matches(db, filters, q or "")
                if found is not None:
                    semantic, search_mode = found, "hybrid"
            condition, rank = _search_condition(db, words)
            id_query = id_query.where(condition)
            if rank is not None:
                order.insert(0, desc(rank))
        ordered = list(db.scalars(id_query.order_by(*order)))
        text_ids = set(ordered)
        if semantic:
            scores = {video_id: 1 / (RRF_K + position) for position, video_id in enumerate(ordered, 1)}
            for position, video_id in enumerate(sorted(semantic, key=lambda key: semantic[key].distance), 1):
                scores[video_id] = scores.get(video_id, 0.0) + 1 / (RRF_K + position)
            ordered = sorted(scores, key=lambda video_id: -scores[video_id])
        page_ids = ordered[offset:offset + limit]
        rows = {video.id: video for video in db.scalars(
            select(Video).options(columns, selectinload(Video.tags)).where(Video.id.in_(page_ids))
        )} if page_ids else {}
        videos = [rows[video_id] for video_id in page_ids if video_id in rows]
        # A video found by meaning only shows its passage: its first word hit could be any "pour" or "les".
        snippets = _snippets(db, [video.id for video in videos[:SNIPPET_RESULTS] if video.id in text_ids], words)
        for video in videos[:SNIPPET_RESULTS]:
            if video.id not in snippets and video.id in semantic:
                snippets[video.id] = semantic_snippet(semantic[video.id])
        jobs = _latest_jobs(db, [video.id for video in videos])
        snapshot = queue_snapshot(db)
        response.headers["X-Search-Mode"] = search_mode
        response.headers["X-Total-Count"] = str(len(ordered))
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


@dataclass
class ImportSettings:
    """Validated options of a new import: the upload form and the watched folder (n°9) share them."""

    target_language: str | None
    template_id: str | None
    custom_prompt: str | None
    summary_length: str
    source_language: str | None
    video_terms: list[str]
    glossary_snapshot: list[str]
    diarize: bool
    num_speakers: int | None
    source_policy: str = "keep"
    tags: list[str] = field(default_factory=list)


def media_suffix(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in VIDEO_SUFFIXES | AUDIO_SUFFIXES:
        raise HTTPException(400, "Format de fichier non pris en charge")
    return suffix


def import_settings(
    *,
    target_language: str | None,
    template_id: str | None,
    custom_prompt: str | None,
    summary_length: str | None,
    source_language: str | None,
    vocabulary: str | None,
    use_global_glossary: bool,
    diarize: bool,
    num_speakers: int | None,
    source_policy: str | None = None,
    tag: str | None = None,
) -> ImportSettings:
    """Check the options; raises HTTPException with the message shown to the user."""
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

    normalized_policy = (source_policy or "").strip() or "keep"
    if normalized_policy not in SOURCE_POLICIES:
        raise HTTPException(422, "Règle de conservation des médias inconnue")
    tags = parse_tags([tag]) if tag else []

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

    return ImportSettings(
        target_language=normalized_target_language,
        template_id=normalized_template_id,
        custom_prompt=normalized_custom_prompt,
        summary_length=normalized_summary_length,
        source_language=normalized_source_language,
        video_terms=video_terms,
        glossary_snapshot=glossary_snapshot,
        diarize=diarize,
        num_speakers=num_speakers if diarize else None,
        source_policy=normalized_policy,
        tags=tags,
    )


def create_import(destination: Path, original_filename: str, options: ImportSettings, video_id: str,
                  audio_layout: str | None = None) -> ProcessingJob:
    """Probe the stored file, create the video and its job, queue it.

    The caller owns `destination`: it removes it (upload) or sets it aside
    (watched folder) when this raises.
    """
    try:
        duration = ffprobe_duration(destination, timeout_seconds=settings.ffprobe_timeout_seconds)
    except Exception:
        raise HTTPException(400, "Fichier multimédia invalide ou illisible par ffprobe")

    if duration > settings.max_video_hours * 3600:
        raise HTTPException(400, f"Durée maximale: {settings.max_video_hours:g} heures")
    return queue_import(
        video_id, options, filename=destination.name, original_filename=original_filename, path=str(destination),
        duration_seconds=duration, size_bytes=destination.stat().st_size, audio_layout=audio_layout,
    )


def queue_import(video_id: str, options: ImportSettings, **video_fields) -> ProcessingJob:
    """Create the video (`video_fields`: file, duration…) and its FULL job, and queue it."""
    job_id = str(uuid.uuid4())
    try:
        with SessionLocal() as db:
            video = Video(
                id=video_id,
                **video_fields,
                status="QUEUED",
                target_language=options.target_language,
                detected_language=options.source_language,
                source_language_forced=options.source_language is not None,
                vocabulary=join_terms(options.video_terms),
                # Frozen at import: later glossary edits never change this video (F-11.13).
                glossary_snapshot=join_terms(options.glossary_snapshot),
                diarize=options.diarize,
                num_speakers=options.num_speakers,
                source_policy=options.source_policy,
            )
            if options.tags:
                existing = {
                    tag.name.casefold(): tag
                    for tag in db.scalars(select(Tag).where(func.lower(Tag.name).in_([name.lower() for name in options.tags])))
                }
                video.tags = [existing.get(name.casefold()) or Tag(name=name) for name in options.tags]
            job = ProcessingJob(
                id=job_id,
                video_id=video_id,
                stage="QUEUED",
                status="QUEUED",
                progress=0,
                template_id=options.template_id,
                custom_prompt=options.custom_prompt,
                summary_length=options.summary_length,
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
                db.flush()
                _delete_orphan_tags(db)
                db.commit()
                raise HTTPException(503, "Service de traitement indisponible, réessayez ultérieurement") from exc

            job.rq_job_id = rq_job.id
            db.commit()
            db.refresh(job)
            return job
    except HTTPException:
        raise
    except Exception:
        logger.exception("Unable to create upload records")
        raise HTTPException(500, "Impossible de créer le traitement vidéo")


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
    source_policy: str | None = Form(None),
):
    video_id = str(uuid.uuid4())
    original_filename = file.filename or "video.bin"
    suffix = media_suffix(original_filename)

    if len(original_filename) > 255:
        raise HTTPException(422, "Le nom du fichier ne peut pas dépasser 255 caractères")

    options = import_settings(
        target_language=target_language,
        template_id=template_id,
        custom_prompt=custom_prompt,
        summary_length=summary_length,
        source_language=source_language,
        vocabulary=vocabulary,
        use_global_glossary=use_global_glossary,
        diarize=diarize,
        num_speakers=num_speakers,
        source_policy=source_policy,
    )

    destination = settings.uploads_dir / f"{video_id}{suffix}"
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
        return create_import(destination, original_filename, options, video_id)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


@app.get("/videos/{video_id}", response_model=VideoDetail)
def get_video(video_id: str):
    with SessionLocal() as db:
        video = db.get(Video, video_id)
        if not video:
            raise HTTPException(404, "Vidéo introuvable")
        _ = video.segments, video.summaries
        job = db.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.video_id == video_id, ProcessingJob.kind.not_in(BACKGROUND_KINDS))
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
            "audio_layout": video.audio_layout,
            "source_policy": video.source_policy,
            "source_url": video.source_url,
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
            .where(ProcessingJob.video_id == video_id, ProcessingJob.kind.not_in(BACKGROUND_KINDS))
            .order_by(desc(ProcessingJob.created_at))
            .with_for_update()
        )
        running_index = list(db.scalars(select(ProcessingJob.rq_job_id).where(
            ProcessingJob.video_id == video_id, ProcessingJob.kind.in_(BACKGROUND_KINDS), ProcessingJob.status == "RUNNING"
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
        # A link import whose download failed fetches its file again.
        if not Path(video.path).is_file() and not video.source_url:
            raise HTTPException(409, "Fichier source introuvable")

        previous_job = db.scalar(
            select(ProcessingJob)
            .where(ProcessingJob.video_id == video_id, ProcessingJob.kind.not_in(BACKGROUND_KINDS))
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


# Server-Sent Events must reach the page piece by piece. "no-transform": the
# Next.js server (the /api proxy) gzips responses otherwise, and gzip held the
# whole stream until its end. X-Accel-Buffering: same for a reverse proxy.
SSE_HEADERS = {"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"}


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

    return StreamingResponse(
        events(), media_type="text/event-stream", headers=SSE_HEADERS
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
    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


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


@app.get("/glossary/suggestions", response_model=list[GlossarySuggestionOut])
def list_glossary_suggestions():
    """Terms corrected by hand several times, not in the glossary yet (n°2)."""
    with SessionLocal() as db:
        return [asdict(suggestion) for suggestion in glossary_suggestions(db)]


@app.post("/glossary/suggestions/accept", response_model=GlossaryOut)
def accept_glossary_suggestion(payload: GlossaryTermIn):
    """Add a suggested term at the end of the global glossary, in one click."""
    try:
        term = parse_glossary([payload.term])[0]
    except (TermsError, IndexError) as exc:
        raise HTTPException(422, str(exc) or "Terme invalide") from exc
    with SessionLocal() as db:
        rows = list(db.scalars(select(GlossaryTerm).order_by(GlossaryTerm.position).with_for_update()))
        if not any(row.term.casefold() == term.casefold() for row in rows):
            if len(rows) >= GLOSSARY_MAX_TERMS:
                raise HTTPException(409, f"Le glossaire est plein ({GLOSSARY_MAX_TERMS} termes) : retirez-en un d'abord")
            db.add(GlossaryTerm(term=term, position=max((row.position for row in rows), default=-1) + 1))
            try:
                db.commit()
            except IntegrityError:  # added meanwhile
                db.rollback()
    return {"terms": _glossary_terms()}


@app.post("/glossary/suggestions/dismiss")
def dismiss_glossary_suggestion(payload: GlossaryTermIn):
    """Never suggest this term again."""
    with SessionLocal() as db:
        dismiss_suggestion(db, payload.term)
        try:
            db.commit()
        except IntegrityError:  # dismissed meanwhile
            db.rollback()
    return {"dismissed": True}


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
        .where(ProcessingJob.video_id == video_id, ProcessingJob.kind.not_in(BACKGROUND_KINDS))
        .order_by(desc(ProcessingJob.created_at))
    )
    if job and job.status in ACTIVE_JOB_STATUSES:
        raise HTTPException(409, ERROR_JOB_IN_PROGRESS)


# The slim image has no /etc/mime.types: Python alone does not know these, and
# some browsers refuse to play an "application/octet-stream" track.
MEDIA_TYPES = {
    ".m4a": "audio/mp4", ".m4v": "video/mp4", ".mkv": "video/x-matroska", ".ogv": "video/ogg",
    ".flac": "audio/flac", ".ogg": "audio/ogg", ".oga": "audio/ogg", ".opus": "audio/ogg",
}


def _media_response(path: Path) -> FileResponse:
    # FileResponse answers HTTP Range requests: the player can seek anywhere.
    media_type = MEDIA_TYPES.get(path.suffix.lower()) or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
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
    mark_entities_stale(db, video.id)
    db.commit()
    if video.status == "COMPLETED":
        write_exports(db, video.id)
        # The chat answers from the passages, the entity pages from the lines: both follow the correction.
        enqueue_index_job(video.id)
        enqueue_entities_job(video.id)


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
            # The glossary learns from the names fixed by hand (n°2).
            record_corrections(db, video_id, [(misheard, term, 1) for misheard, term in corrections_between(segment.text, payload.text)])
            segment.text = payload.text
            # Corrected by hand: nothing left to doubt (n°1).
            segment.doubts = None
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
                segment.doubts = None  # the ranges no longer match the text
                replaced += count
                changed += 1
        if replaced:
            pair = replacement_pair(payload.find, payload.replace)
            if pair:
                record_corrections(db, video_id, [(*pair, replaced)])
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
        if not Path(video.path).is_file() and not _audio_path(video_id).is_file():
            raise HTTPException(409, "Les médias de cette vidéo ont été supprimés : les intervenants ne peuvent plus être identifiés")
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


def _save_library_exchange(conversation_id: str, question: str, answer: str, sources: list[dict], *, interrupted: bool) -> str | None:
    """Save the question and its answer; returns the answer's id (None: the conversation was deleted meanwhile)."""
    answer_id = str(uuid.uuid4())
    with SessionLocal() as db:
        conversation = db.get(LibraryConversation, conversation_id)
        if conversation is None:
            return None
        now = utcnow()
        db.add(LibraryMessage(id=str(uuid.uuid4()), conversation_id=conversation_id, role="user", content=question, created_at=now))
        db.add(LibraryMessage(
            id=answer_id, conversation_id=conversation_id, role="assistant", content=answer,
            sources=json.dumps(sources, ensure_ascii=False), interrupted=interrupted, created_at=now + timedelta(milliseconds=1),
        ))
        conversation.updated_at = now
        db.commit()
    return answer_id


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
        saved: str | None = None

        def keep(interrupted: bool) -> bool:
            nonlocal saved
            answer = "".join(pieces).strip()
            if not saved and answer:
                saved = _save_library_exchange(conversation_id, payload.question, answer, sources, interrupted=interrupted)
            return saved is not None

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
            yield _sse("done", {"answer": answer, "conversation_id": conversation_id, "message_id": saved})
        except Exception:
            logger.exception("Unable to stream a library answer")
            yield _sse("error", {"detail": ERROR_CHAT_UNAVAILABLE, "saved": keep(True)})
        finally:
            keep(True)
            if prepared["is_new"] and not saved:
                _discard_empty_conversation(conversation_id)

    return StreamingResponse(
        events(), media_type="text/event-stream", headers=SSE_HEADERS
    )


@app.get("/library/conversations", response_model=list[LibraryConversationOut])
def list_conversations(q: str | None = Query(None, max_length=200), flagged: bool = False):
    """Most recent first; `q` searches the titles and messages, `flagged` keeps those with an answer rated wrong."""
    with SessionLocal() as db:
        statement = select(LibraryConversation).order_by(desc(LibraryConversation.updated_at)).limit(CONVERSATIONS_LIMIT)
        for word in search_words(q):
            pattern = f"%{word}%"
            statement = statement.where(or_(
                func.lower(LibraryConversation.title).like(pattern),
                exists().where(LibraryMessage.conversation_id == LibraryConversation.id, func.lower(LibraryMessage.content).like(pattern)),
            ))
        if flagged:
            statement = statement.where(exists().where(
                LibraryMessage.conversation_id == LibraryConversation.id, LibraryMessage.feedback == -1
            ))
        rows = list(db.scalars(statement))
        flags = set(db.scalars(select(LibraryMessage.conversation_id).where(
            LibraryMessage.conversation_id.in_([row.id for row in rows]), LibraryMessage.feedback == -1
        )))
        return [{**_conversation_out(conversation), "flagged": conversation.id in flags} for conversation in rows]


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
                    "interrupted": message.interrupted, "feedback": message.feedback, "created_at": message.created_at,
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


# --- Disk space (n°14) -------------------------------------------------------------------

@app.get("/storage")
def storage_report():
    """What each video occupies on disk, largest first, and the disk itself."""
    with SessionLocal() as db:
        videos = list(db.scalars(select(Video).options(load_only(
            Video.id, Video.original_filename, Video.path, Video.status, Video.duration_seconds,
            Video.created_at, Video.source_policy,
        ))))
        jobs = _latest_jobs(db, [video.id for video in videos])
        rows = []
        for video in videos:
            job = jobs.get(video.id)
            rows.append({
                "id": video.id,
                "original_filename": video.original_filename,
                "status": video.status,
                "duration_seconds": video.duration_seconds,
                "created_at": video.created_at,
                "source_policy": video.source_policy,
                "busy": bool(job and job.status in ACTIVE_JOB_STATUSES),
                **video_usage(video),
            })
    rows.sort(key=lambda row: row["total_bytes"], reverse=True)
    totals = {
        key: sum(row[key] for row in rows) for key in ("source_bytes", "audio_bytes", "exports_bytes", "total_bytes")
    }
    return {**disk_summary(), "totals": totals, "videos": rows}


@app.post("/videos/{video_id}/storage")
def free_video_storage(video_id: str, payload: StorageActionIn):
    """Free a processed video's media; its text (transcript, summary, exports) stays."""
    with SessionLocal() as db:
        video = _video_or_404(db, video_id, lock=True)
        if video.status != "COMPLETED":
            raise HTTPException(409, "Seule une vidéo traitée peut libérer ses médias ; sinon, supprimez-la")
        _refuse_if_busy(db, video_id)
        if payload.action == "audio":
            source = Path(video.path)
            if not source.is_file():
                raise HTTPException(409, "Fichier source introuvable")
            if source.suffix.lower() in COMPRESSED_AUDIO_SUFFIXES:
                raise HTTPException(409, "La source est déjà un fichier audio compressé")
            job = ProcessingJob(id=str(uuid.uuid4()), video_id=video_id, kind="COMPACT", stage="QUEUED", status="QUEUED", progress=0)
            db.add(job)
            db.commit()
            try:
                queue = Queue(QUEUE_NAME, connection=Redis.from_url(settings.redis_url), default_timeout=21600)
                rq_job = queue.enqueue("app.worker.run_compact", job.id, job_timeout=21600, result_ttl=86400)
            except Exception as exc:
                logger.warning("Unable to enqueue compact job %s: %s", job.id, exc)
                db.delete(job)
                db.commit()
                raise HTTPException(503, "Service de traitement indisponible, réessayez ultérieurement") from exc
            job.rq_job_id = rq_job.id
            db.commit()
            db.refresh(job)
            return {"job": _job_payload(job, {}), "freed_bytes": 0, "usage": video_usage(video)}
        try:
            if payload.action == "delete_media":
                freed = delete_media(video)
            elif payload.action == "delete_work_audio":
                freed = delete_work_audio(video)
            else:
                raise HTTPException(422, "Action inconnue")
        except StorageError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"job": None, "freed_bytes": freed, "usage": video_usage(video)}


# --- Watched folder (n°9) ----------------------------------------------------------------

def _watch_folder_options(config: WatchFolderSettings) -> None:
    """The defaults must be importable: the same rules as the upload form."""
    import_settings(
        target_language=config.target_language, template_id=config.template_id, custom_prompt=None,
        summary_length=config.summary_length, source_language=config.source_language, vocabulary=None,
        use_global_glossary=False, diarize=config.diarize, num_speakers=config.num_speakers,
        source_policy=config.source_policy, tag=config.tag,
    )


@app.get("/settings/watch-folder", response_model=WatchFolderSettings)
def get_watch_folder_settings():
    with SessionLocal() as db:
        return app_settings.load(db, app_settings.WATCH_FOLDER, WatchFolderSettings)


@app.put("/settings/watch-folder", response_model=WatchFolderSettings)
def put_watch_folder_settings(payload: WatchFolderSettings):
    config = payload.model_copy(update={
        "target_language": payload.target_language or None,
        "template_id": payload.template_id or None,
        "source_language": (payload.source_language or "").lower() or None,
        "tag": " ".join((payload.tag or "").split()) or None,
        "num_speakers": payload.num_speakers if payload.diarize else None,
    })
    _watch_folder_options(config)
    with SessionLocal() as db:
        app_settings.save(db, app_settings.WATCH_FOLDER, config)
        db.commit()
    return config


@app.get("/watch-folder")
def watch_folder_state():
    """Files waiting in the inbox and files it refused, with their reason."""
    with SessionLocal() as db:
        config = app_settings.load(db, app_settings.WATCH_FOLDER, WatchFolderSettings)
    return {
        "enabled": config.enabled,
        "folder": "data/inbox",
        "stable_seconds": settings.watch_stable_seconds,
        "pending": watch_folder.pending_files(),
        "rejected": watch_folder.rejected_files(),
    }


@app.post("/watch-folder/rejected/{name}/retry")
def retry_rejected_file(name: str):
    try:
        watch_folder.retry_rejected(name)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Fichier introuvable") from exc
    return {"retried": True}


@app.delete("/watch-folder/rejected/{name}")
def delete_rejected_file(name: str):
    try:
        watch_folder.delete_rejected(name)
    except FileNotFoundError as exc:
        raise HTTPException(404, "Fichier introuvable") from exc
    return {"deleted": True}


# --- Backups and library archive (n°13) --------------------------------------------------

def _backup_out(info: backups.BackupInfo) -> dict:
    return {"name": info.name, "kind": info.kind, "size_bytes": info.size_bytes, "created_at": info.created_at}


@app.get("/backups")
def list_backups():
    with SessionLocal() as db:
        config = app_settings.load(db, app_settings.BACKUPS, BackupSettings)
    status = backups.read_status()
    return {
        "settings": config.model_dump(),
        "folder": "data/backups",
        "backups": [_backup_out(info) for info in backups.list_backups()],
        "last_error": status.get("last_error"),
        "last_error_at": datetime.fromtimestamp(status["last_error_at"], timezone.utc) if status.get("last_error_at") else None,
        "last_success_at": datetime.fromtimestamp(status["last_success_at"], timezone.utc) if status.get("last_success_at") else None,
    }


@app.put("/settings/backups", response_model=BackupSettings)
def put_backup_settings(payload: BackupSettings):
    with SessionLocal() as db:
        app_settings.save(db, app_settings.BACKUPS, payload)
        db.commit()
    return payload


@app.post("/backups")
async def create_backup_now():
    try:
        info = await run_in_threadpool(backups.create_backup, "manuel")
    except backups.BackupBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except backups.BackupError as exc:
        raise HTTPException(500, str(exc)) from exc
    return _backup_out(info)


@app.get("/backups/{name}")
def download_backup(name: str):
    try:
        path = backups.backup_path(name)
    except backups.BackupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


@app.delete("/backups/{name}")
def delete_backup(name: str):
    try:
        backups.delete_backup(name)
    except backups.BackupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"deleted": True}


@app.get("/library/export")
def export_library(media: bool = False):
    """The processed videos, glossary, templates and conversations as one tar, streamed."""
    filename = portable.export_filename()
    return StreamingResponse(
        portable.export_stream(media), media_type="application/x-tar",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/library/import")
async def import_library(file: UploadFile = File(...)):
    """Merge a library archive into this one: videos already present are skipped."""
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    temporary = settings.uploads_dir / f".import-{uuid.uuid4()}.tar"
    written = 0
    try:
        with temporary.open("wb") as out:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                written += len(chunk)
                if written > settings.max_import_bytes:
                    raise HTTPException(413, "Archive trop volumineuse : importez-la en ligne de commande (voir la documentation)")
                out.write(chunk)
        await file.close()
        try:
            return await run_in_threadpool(portable.import_archive, temporary)
        except portable.ArchiveError as exc:
            raise HTTPException(422, str(exc)) from exc
    finally:
        temporary.unlink(missing_ok=True)


# --- Recordings from the browser (n°10) and live transcript (n°11) -------------------------

# What MediaRecorder produces, and the container the recording is kept in:
# Opus (Chrome, Firefox) in Ogg, AAC (Safari) in MP4, both played by the audio player.
RECORDING_CONTAINERS = {"audio/webm": ".webm", "video/webm": ".webm", "audio/ogg": ".ogg", "audio/mp4": ".mp4", "video/mp4": ".mp4"}
RECORDING_CHUNK_MAX_BYTES = 16 * 1024 * 1024
RECORDING_EVENTS_POLL_SECONDS = 1.0


def _import_options(payload: ImportOptionsIn, *, tag: str | None = None) -> ImportSettings:
    return import_settings(
        target_language=payload.target_language, template_id=payload.template_id, custom_prompt=payload.custom_prompt,
        summary_length=payload.summary_length, source_language=payload.source_language, vocabulary=payload.vocabulary,
        use_global_glossary=payload.use_global_glossary, diarize=payload.diarize, num_speakers=payload.num_speakers,
        source_policy=payload.source_policy, tag=tag,
    )


def _recording_or_404(db, recording_id: str, *, lock: bool = False) -> Recording:
    query = select(Recording).where(Recording.id == recording_id)
    recording = db.scalar(query.with_for_update() if lock else query)
    if recording is None:
        raise HTTPException(404, "Enregistrement introuvable")
    return recording


def _filename_for(title: str, suffix: str) -> str:
    stem = re.sub(r'[\\/:*?"<>|]+', " ", title).strip()[:200] or "Enregistrement"
    return f"{stem}{suffix}"


@app.post("/recordings", response_model=RecordingOut)
def start_recording(payload: RecordingCreate):
    container = RECORDING_CONTAINERS.get(payload.mime_type.split(";")[0].strip().lower())
    if container is None:
        raise HTTPException(422, "Format d'enregistrement non pris en charge par Sténo")
    language = (payload.language or "").lower() or None
    if language and language not in SOURCE_LANGUAGES:
        raise HTTPException(422, "Langue source non prise en charge")
    recording_id = str(uuid.uuid4())
    settings.uploads_dir.mkdir(parents=True, exist_ok=True)
    path = settings.uploads_dir / f".rec-{recording_id}{container}"
    path.touch()
    now = utcnow()
    recording = Recording(
        id=recording_id, title=payload.title, status="RECORDING", live=payload.live, sides=payload.sides,
        mime_type=payload.mime_type,
        path=str(path), size_bytes=0, chunks=0, language=language, created_at=now, updated_at=now,
    )
    with SessionLocal() as db:
        db.add(recording)
        db.commit()
        db.refresh(recording)
        return recording


@app.get("/recordings", response_model=list[RecordingOut])
def unfinished_recordings():
    """Recordings still open: a tab closed by mistake leaves one, to finish or discard."""
    with SessionLocal() as db:
        return list(db.scalars(select(Recording).where(Recording.status == "RECORDING").order_by(desc(Recording.updated_at))))


@app.get("/recordings/{recording_id}", response_model=RecordingOut)
def get_recording(recording_id: str):
    with SessionLocal() as db:
        return _recording_or_404(db, recording_id)


@app.put("/recordings/{recording_id}/chunks/{index}", response_model=RecordingOut)
async def append_recording_chunk(recording_id: str, index: int, request: Request):
    """Chunks arrive in order; a chunk sent twice (a retry) is acknowledged without being written again."""
    data = await request.body()
    if len(data) > RECORDING_CHUNK_MAX_BYTES:
        raise HTTPException(413, "Morceau d'enregistrement trop volumineux")

    def append():
        with SessionLocal() as db:
            recording = _recording_or_404(db, recording_id, lock=True)
            if recording.status != "RECORDING":
                raise HTTPException(409, "Cet enregistrement est terminé")
            if index < recording.chunks:
                return recording
            if index > recording.chunks:
                raise HTTPException(409, f"Morceau {index} reçu avant le morceau {recording.chunks}")
            if recording.size_bytes + len(data) > settings.max_upload_bytes:
                raise HTTPException(413, "Enregistrement trop volumineux : arrêtez-le pour l'analyser")
            with open(recording.path, "ab") as out:
                out.write(data)
            recording.chunks += 1
            recording.size_bytes += len(data)
            recording.updated_at = utcnow()
            db.commit()
            db.refresh(recording)
            return recording

    return await run_in_threadpool(append)


def _remux_recording(source: Path, destination: Path) -> None:
    """MediaRecorder streams carry no duration: rewrite them into a proper file, without re-encoding if possible."""
    container = ["-movflags", "+faststart", "-f", "mp4"] if destination.suffix == ".m4a" else ["-f", "ogg"]
    base = ["ffmpeg", "-y", "-nostdin", "-loglevel", "error", "-i", str(source), "-vn"]
    try:
        subprocess.run([*base, "-c:a", "copy", *container, str(destination)], check=True, capture_output=True,
                       timeout=settings.ffmpeg_timeout_seconds)
    except subprocess.CalledProcessError:
        # A codec the container does not take as is: encode it.
        codec = ["-c:a", "aac", "-b:a", "96k"] if destination.suffix == ".m4a" else ["-c:a", "libopus", "-b:a", "48k"]
        subprocess.run([*base, *codec, *container, str(destination)], check=True, capture_output=True,
                       timeout=settings.ffmpeg_timeout_seconds)


@app.post("/recordings/{recording_id}/finish", response_model=JobOut)
async def finish_recording(recording_id: str, payload: ImportOptionsIn):
    """Stop the recording and analyse it like any imported file."""
    options = _import_options(payload)
    with SessionLocal() as db:
        recording = _recording_or_404(db, recording_id)
        if recording.status != "RECORDING":
            raise HTTPException(409, "Cet enregistrement est déjà terminé")
        if not recording.size_bytes:
            raise HTTPException(409, "L'enregistrement est vide")
        source, title, sides = Path(recording.path), recording.title, recording.sides
    video_id = str(uuid.uuid4())
    suffix = ".m4a" if source.suffix == ".mp4" else ".ogg"
    destination = settings.uploads_dir / f"{video_id}{suffix}"
    try:
        await run_in_threadpool(_remux_recording, source, destination)
    except Exception as exc:
        destination.unlink(missing_ok=True)
        logger.warning("Unable to remux recording %s", recording_id, exc_info=True)
        raise HTTPException(422, "L'enregistrement est illisible ; il est conservé, vous pouvez réessayer ou l'abandonner") from exc
    try:
        job = await run_in_threadpool(
            create_import, destination, _filename_for(title, suffix), options, video_id, "sides" if sides else None,
        )
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    with SessionLocal() as db:
        recording = _recording_or_404(db, recording_id)
        recording.status, recording.video_id, recording.updated_at = "FINISHED", video_id, utcnow()
        db.commit()
    source.unlink(missing_ok=True)
    return job


@app.delete("/recordings/{recording_id}")
def discard_recording(recording_id: str):
    with SessionLocal() as db:
        recording = _recording_or_404(db, recording_id, lock=True)
        if recording.status == "FINISHED":
            raise HTTPException(409, "Cet enregistrement a déjà été analysé : supprimez plutôt la vidéo")
        Path(recording.path).unlink(missing_ok=True)
        recording.status, recording.updated_at = "CANCELLED", utcnow()
        db.execute(delete(LiveSegment).where(LiveSegment.recording_id == recording_id))
        db.commit()
    return {"deleted": True}


@app.get("/recordings/{recording_id}/live")
async def live_transcript(recording_id: str):
    """The live transcript as Server-Sent Events: `segment` {start, end, text}, then `end` {status}."""
    with SessionLocal() as db:
        _recording_or_404(db, recording_id)

    async def stream():
        last_id = 0
        while True:
            with SessionLocal() as db:
                recording = db.get(Recording, recording_id)
                rows = list(db.scalars(
                    select(LiveSegment).where(LiveSegment.recording_id == recording_id, LiveSegment.id > last_id).order_by(LiveSegment.id)
                ))
                state = {"status": recording.status, "live": recording.live, "error": recording.live_error} if recording else None
            for row in rows:
                last_id = row.id
                yield _sse("segment", {"start": row.start_seconds, "end": row.end_seconds, "text": row.text})
            if state is None or state["status"] != "RECORDING":
                yield _sse("end", state or {"status": "CANCELLED"})
                return
            if state["error"]:
                yield _sse("state", state)
            await asyncio.sleep(RECORDING_EVENTS_POLL_SECONDS)

    return StreamingResponse(stream(), media_type="text/event-stream", headers=SSE_HEADERS)


# --- Import from a link (n°12) -------------------------------------------------------------

@app.get("/settings/url-import", response_model=UrlImportSettings)
def get_url_import_settings():
    with SessionLocal() as db:
        return app_settings.load(db, app_settings.URL_IMPORT, UrlImportSettings)


@app.put("/settings/url-import", response_model=UrlImportSettings)
def put_url_import_settings(payload: UrlImportSettings):
    """Video platforms (yt-dlp): the user's choice, and their responsibility for the rights."""
    with SessionLocal() as db:
        app_settings.save(db, app_settings.URL_IMPORT, payload)
        db.commit()
    return payload


@app.post("/imports/url/preview")
async def preview_url(payload: UrlPreviewIn):
    """What the link points to: a file (name, size) or a podcast feed (its episodes)."""
    try:
        return await run_in_threadpool(url_import.probe, payload.url)
    except url_import.UrlImportError as exc:
        raise HTTPException(422, str(exc)) from exc


@app.post("/imports/url", response_model=JobOut)
async def import_url(payload: UrlImportIn):
    """Queue the analysis of a linked file: the worker downloads it first."""
    try:
        url = await run_in_threadpool(url_import.check_url, payload.url)
    except url_import.UrlImportError as exc:
        raise HTTPException(422, str(exc)) from exc
    options = _import_options(payload)
    video_id = str(uuid.uuid4())
    name = (payload.title or "").strip() or unquote(Path(urlsplit(url).path).name) or "Import depuis un lien"
    return await run_in_threadpool(
        lambda: queue_import(
            video_id, options, filename="", original_filename=name[:255], path=str(settings.uploads_dir / f"{video_id}.download"),
            duration_seconds=0.0, size_bytes=0, source_url=url,
        )
    )


# --- People, organisations, places, dates (n°16) -----------------------------------------------

def _entity_or_404(db, entity_id: int) -> Entity:
    entity = db.get(Entity, entity_id)
    if entity is None:
        raise HTTPException(404, "Fiche introuvable")
    return entity


def _entity_out(entity: Entity, videos: int = 0, mentions: int = 0) -> dict:
    return {
        "id": entity.id, "name": entity.name, "kind": entity.kind, "kind_label": entities.KIND_LABELS.get(entity.kind, entity.kind),
        "hidden": entity.hidden, "videos": videos, "mentions": mentions,
    }


@app.get("/entities")
def list_entities(
    kind: str | None = Query(None, max_length=16), q: str | None = Query(None, max_length=120), hidden: bool = False,
):
    """Everyone and everything named in the library, the most widespread first."""
    if kind and kind not in entities.KINDS:
        raise HTTPException(422, "Type inconnu")
    with SessionLocal() as db:
        rows = entities.counts(db, kind=kind, query=q, include_hidden=hidden)
        return [_entity_out(entity, videos, mentions) for entity, videos, mentions in rows if hidden or not entity.hidden]


@app.get("/entities/progress")
def entities_progress():
    """How many processed videos have been read for entities (the catch-up runs in the background)."""
    with SessionLocal() as db:
        total = db.scalar(select(func.count()).select_from(Video).where(Video.status == "COMPLETED", Video.transcript_text.is_not(None))) or 0
        ready = db.scalar(select(func.count()).select_from(VideoEntityState).where(VideoEntityState.status == "READY")) or 0
        failed = db.scalar(select(func.count()).select_from(VideoEntityState).where(VideoEntityState.status == "FAILED")) or 0
        waiting = db.scalar(select(func.count()).select_from(ProcessingJob).where(
            ProcessingJob.kind == "ENTITIES", ProcessingJob.status.in_(ACTIVE_JOB_STATUSES)
        )) or 0
    return {"videos": total, "ready": min(ready, total), "failed": failed, "waiting": waiting}


@app.get("/entities/{entity_id}")
def get_entity(entity_id: int):
    """Everything said about an entity: its mentions, video by video, most recent video first."""
    with SessionLocal() as db:
        entity = _entity_or_404(db, entity_id)
        rows = db.execute(
            select(EntityMention, Video.original_filename, Video.created_at, Video.duration_seconds)
            .join(Video, Video.id == EntityMention.video_id)
            .where(EntityMention.entity_id == entity_id)
            .order_by(desc(Video.created_at), EntityMention.start_seconds)
        ).all()
        videos: dict[str, dict] = {}
        for mention, title, created_at, duration in rows:
            video = videos.setdefault(mention.video_id, {
                "video_id": mention.video_id, "title": title, "created_at": created_at, "duration_seconds": duration, "mentions": [],
            })
            video["mentions"].append({"start_seconds": mention.start_seconds, "context": mention.context})
        merged_into = db.get(Entity, entity.merged_into) if entity.merged_into else None
        return {
            **_entity_out(entity, len(videos), len(rows)),
            "merged_into": _entity_out(merged_into) if merged_into else None,
            # "videos" is the count (as in the list): the mentions, video by video, are the appearances.
            "appearances": list(videos.values()),
        }


@app.patch("/entities/{entity_id}")
def update_entity(entity_id: int, payload: EntityUpdateIn):
    """Rename (the display name; the extraction still finds it by its folded name), hide or show."""
    with SessionLocal() as db:
        entity = _entity_or_404(db, entity_id)
        if payload.name is not None:
            entity.name = payload.name
        if payload.hidden is not None:
            entity.hidden = payload.hidden
            if not payload.hidden:
                entity.merged_into = None
        db.commit()
        return _entity_out(entity)


@app.post("/entities/{entity_id}/merge")
def merge_entity(entity_id: int, payload: EntityMergeIn):
    """Two spellings of one name: this entity's mentions go to `into`, and its future ones too."""
    if payload.into == entity_id:
        raise HTTPException(422, "Choisissez une autre fiche")
    with SessionLocal() as db:
        source = _entity_or_404(db, entity_id)
        target = _entity_or_404(db, payload.into)
        if target.merged_into:
            raise HTTPException(409, "Cette fiche a elle-même été fusionnée")
        entities.merge(db, source, target)
        db.commit()
        return _entity_out(target)


@app.get("/videos/{video_id}/entities")
def video_entities(video_id: str):
    """The entities named in a video, with their first moment (video page chips)."""
    with SessionLocal() as db:
        _video_or_404(db, video_id)
        rows = db.execute(
            select(Entity, func.count(EntityMention.id), func.min(EntityMention.start_seconds))
            .join(EntityMention, EntityMention.entity_id == Entity.id)
            .where(EntityMention.video_id == video_id, Entity.hidden.is_(False))
            .group_by(Entity.id)
            .order_by(Entity.kind, func.min(EntityMention.start_seconds))
        ).all()
        state = db.get(VideoEntityState, video_id)
        return {
            "status": state.status if state else None,
            "entities": [{**_entity_out(entity, 1, count), "first_seconds": first} for entity, count, first in rows],
        }


# --- Saved searches (n°17) --------------------------------------------------------------------

def _search_out(row: SavedSearch) -> dict:
    return {"id": row.id, "name": row.name, "query": row.query, "created_at": row.created_at}


def _commit_search(db) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Une collection porte déjà ce nom") from exc


@app.get("/library/searches")
def list_saved_searches():
    with SessionLocal() as db:
        return [_search_out(row) for row in db.scalars(select(SavedSearch).order_by(func.lower(SavedSearch.name)))]


@app.post("/library/searches")
def save_search(payload: SavedSearchIn):
    with SessionLocal() as db:
        row = SavedSearch(name=payload.name, query=payload.query.lstrip("?"))
        db.add(row)
        _commit_search(db)
        db.refresh(row)
        return _search_out(row)


@app.patch("/library/searches/{search_id}")
def rename_saved_search(search_id: int, payload: SavedSearchIn):
    with SessionLocal() as db:
        row = db.get(SavedSearch, search_id)
        if row is None:
            raise HTTPException(404, "Collection introuvable")
        row.name = payload.name
        if payload.query:
            row.query = payload.query.lstrip("?")
        _commit_search(db)
        return _search_out(row)


@app.delete("/library/searches/{search_id}")
def delete_saved_search(search_id: int):
    with SessionLocal() as db:
        row = db.get(SavedSearch, search_id)
        if row is None:
            raise HTTPException(404, "Collection introuvable")
        db.delete(row)
        db.commit()
    return {"deleted": True}


# --- Conversations: rename, export, thumbs (n°18) -----------------------------------------------

def _markdown_response(markdown: str, title: str, kind: str) -> Response:
    stem = re.sub(r"[^\w\- ]+", "_", title)[:80].strip() or kind
    ascii_name = stem.encode("ascii", "ignore").decode() or kind
    disposition = f'attachment; filename="{ascii_name} - {kind}.md"; filename*=UTF-8\'\'{quote(f"{stem} - {kind}.md")}'
    return Response(markdown, media_type="text/markdown; charset=utf-8", headers={"Content-Disposition": disposition})


def _answer_markdown(content: str, interrupted: bool, feedback: int | None) -> list[str]:
    lines = [content.strip()]
    if interrupted:
        lines.append("\n*Réponse interrompue : seul le début a été écrit.*")
    if feedback == -1:
        lines.append("\n*Réponse signalée comme incorrecte.*")
    return lines


@app.patch("/library/conversations/{conversation_id}")
def rename_conversation(conversation_id: str, payload: ConversationRenameIn):
    with SessionLocal() as db:
        conversation = _conversation_or_404(db, conversation_id)
        conversation.title = payload.title
        db.commit()
        return _conversation_out(conversation)


@app.get("/library/conversations/{conversation_id}/export")
def export_conversation(conversation_id: str):
    """The conversation as Markdown: questions, answers, and the passages each answer cites."""
    with SessionLocal() as db:
        conversation = _conversation_or_404(db, conversation_id)
        count = len(json.loads(conversation.video_ids))
        lines = [
            f"# {conversation.title}", "",
            f"Conversation Sténo · {conversation.scope or 'toute la bibliothèque'} · {count} vidéo{'s' if count > 1 else ''} · "
            f"{_as_aware(conversation.created_at):%d/%m/%Y}",
        ]
        for message in conversation.messages:
            if message.role == "user":
                lines += ["", "## Question", "", message.content.strip()]
                continue
            lines += ["", "### Réponse", "", *_answer_markdown(message.content, message.interrupted, message.feedback)]
            sources = json.loads(message.sources) if message.sources else []
            if sources:
                lines += ["", "Sources :", ""]
                lines += [f"{source['n']}. {source.get('title') or 'Vidéo'} — {timestamp(source['start_seconds'])}" for source in sources]
        return _markdown_response("\n".join(lines) + "\n", conversation.title, "conversation")


@app.put("/library/messages/{message_id}/feedback")
def rate_library_answer(message_id: str, payload: FeedbackIn):
    with SessionLocal() as db:
        message = db.get(LibraryMessage, message_id)
        if message is None or message.role != "assistant":
            raise HTTPException(404, "Réponse introuvable")
        message.feedback = payload.value
        db.commit()
    return {"feedback": payload.value}


@app.put("/videos/{video_id}/chat/messages/{message_id}/feedback")
def rate_video_answer(video_id: str, message_id: str, payload: FeedbackIn):
    with SessionLocal() as db:
        message = db.get(VideoChatMessage, message_id)
        if message is None or message.video_id != video_id or message.role != "assistant":
            raise HTTPException(404, "Réponse introuvable")
        message.feedback = payload.value
        db.commit()
    return {"feedback": payload.value}


@app.get("/videos/{video_id}/chat/export")
def export_video_chat(video_id: str):
    with SessionLocal() as db:
        video = _video_or_404(db, video_id)
        lines = [f"# Questions sur « {video.original_filename} »", ""]
        for message in video.chat_messages:
            if message.role == "user":
                lines += ["## Question", "", message.content.strip(), ""]
            else:
                lines += ["### Réponse", "", *_answer_markdown(message.content, message.interrupted, message.feedback), ""]
        return _markdown_response("\n".join(lines), video.original_filename, "questions")


# --- Models (n°20) ------------------------------------------------------------------------------

OLLAMA_TIMEOUT_SECONDS = 5.0
BENCHMARK_PROMPT = (
    "Résume en trois phrases, en français, ce passage de réunion : « Nous avons revu le budget du trimestre. "
    "Le poste marketing dépasse de 12 % ; Claire propose de décaler la campagne de juin à septembre. "
    "Karim valide, à condition que le lancement du produit reste au 15 octobre. Prochaine réunion lundi. »"
)
WHISPER_SAMPLE_SECONDS = 60


def _ollama(method: str, path: str, **kwargs):
    with httpx.Client(timeout=kwargs.pop("timeout", OLLAMA_TIMEOUT_SECONDS)) as client:
        response = client.request(method, f"{settings.ollama_url.rstrip('/')}{path}", **kwargs)
        response.raise_for_status()
        return response.json() if response.content else {}


def _installed_models() -> list[dict]:
    rows = _ollama("GET", "/api/tags").get("models", [])
    return [
        {
            "name": row.get("name") or row.get("model"),
            "size_bytes": row.get("size"),
            "parameter_size": (row.get("details") or {}).get("parameter_size"),
            "quantization": (row.get("details") or {}).get("quantization_level"),
            "family": (row.get("details") or {}).get("family"),
            "modified_at": row.get("modified_at"),
            "embedding": (row.get("name") or "").split(":")[0] == settings.embedding_model.split(":")[0]
            or (row.get("details") or {}).get("family") in ("bert", "nomic-bert"),
        }
        for row in rows
    ]


def _redis_json(key: str) -> dict | None:
    try:
        raw = Redis.from_url(settings.redis_url, socket_connect_timeout=1, socket_timeout=1).get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


def _whisper_cached(name: str) -> bool:
    """Whether the worker already downloaded this Whisper model (shared cache volume, read-only here)."""
    hub = Path.home() / ".cache" / "huggingface" / "hub"
    try:
        from faster_whisper.utils import _MODELS

        repository = _MODELS.get(name, "")
    except Exception:
        repository = ""
    if repository:
        return (hub / f"models--{repository.replace('/', '--')}").is_dir()
    return any(hub.glob(f"models--*faster-whisper-{name}"))


def _same_model(a: str, b: str) -> bool:
    tag = lambda name: name if ":" in name else f"{name}:latest"  # noqa: E731
    return tag(a) == tag(b)


@app.get("/models")
def models_overview():
    """Models in use, installed, suggested; GPU memory when a GPU service reports it."""
    try:
        installed, ollama_error = _installed_models(), None
        loaded = [
            {"name": row.get("name"), "size_bytes": row.get("size"), "vram_bytes": row.get("size_vram"), "expires_at": row.get("expires_at")}
            for row in _ollama("GET", "/api/ps").get("models", [])
        ]
    except Exception:
        logger.warning("Ollama unavailable for the models page", exc_info=True)
        installed, loaded, ollama_error = [], [], "Ollama ne répond pas"
    names = [row["name"] for row in installed]
    gpu = _redis_json(GPU_KEY)
    return {
        "llm": {"current": ai_models.llm_model(), "default": settings.llm_model, "chosen": ai_models.chosen().llm_model is not None},
        "whisper": {
            "current": ai_models.whisper_model(), "default": settings.whisper_model,
            "chosen": ai_models.chosen().whisper_model is not None, "live": settings.live_whisper_model,
            "device": (gpu or {}).get("whisper_device") or settings.whisper_device,
            "choices": [{**choice, "cached": _whisper_cached(choice["name"])} for choice in ai_models.WHISPER_CHOICES],
        },
        "embedding_model": settings.embedding_model,
        "installed": installed,
        "suggestions": [
            {**suggestion, "installed": any(_same_model(suggestion["name"], name) for name in names)}
            for suggestion in ai_models.LLM_SUGGESTIONS
        ],
        "loaded": loaded,
        "gpu": gpu,
        "ollama_error": ollama_error,
    }


@app.put("/settings/models")
def choose_models(payload: ModelSettings):
    """The LLM must be installed and not an embedding model; the Whisper model one of the known sizes."""
    llm_model = (payload.llm_model or "").strip() or None
    whisper_model = (payload.whisper_model or "").strip() or None
    if whisper_model and whisper_model not in ai_models.WHISPER_NAMES:
        raise HTTPException(422, "Modèle de transcription inconnu")
    if llm_model:
        try:
            installed = _installed_models()
        except Exception as exc:
            raise HTTPException(503, "Ollama ne répond pas : impossible de vérifier le modèle") from exc
        match = next((row for row in installed if _same_model(row["name"], llm_model)), None)
        if match is None:
            raise HTTPException(422, "Ce modèle n'est pas installé : téléchargez-le d'abord")
        if match["embedding"]:
            raise HTTPException(422, "C'est un modèle d'embeddings (recherche), pas un modèle de langage")
    value = ModelSettings(llm_model=llm_model, whisper_model=whisper_model)
    with SessionLocal() as db:
        app_settings.save(db, app_settings.MODELS, value)
        db.commit()
    ai_models.forget()
    try:
        # New models: the reference corpus is replayed to compare (n°4), when it is on this machine.
        quality.maybe_schedule("auto")
    except Exception:
        logger.warning("Unable to schedule a quality run", exc_info=True)
    return {"llm_model": ai_models.llm_model(), "whisper_model": ai_models.whisper_model()}


# Video memory a language model needs at Sténo's 32k context, from its file size
# (estimate): measured 11.3 GB for qwen3:8b (a 5.2 GB file).
VRAM_PER_FILE_BYTE = 1.3
VRAM_CONTEXT_BYTES = 4 * 1024 ** 3
# Left to the desktop, the live transcription and Whisper's own buffers.
VRAM_RESERVED_BYTES = 2 * 1024 ** 3


def _catalog_redis():
    return Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=5)


def _installed_names() -> set[str]:
    try:
        return {row["name"] for row in _installed_models()}
    except Exception:
        return set()


@app.get("/models/catalog")
def models_catalog(refresh: bool = False):
    """The models of ollama.com (fetched every 6 hours), those installed marked; cloud-only models left out."""
    result = ollama_catalog.library(_catalog_redis(), refresh=refresh)
    installed = {name.split(":")[0] for name in _installed_names()}
    result["models"] = [
        model | {"installed": model["name"] in installed}
        for model in result["models"]
        # Sténo runs everything on this computer: a model served only by Ollama's cloud is of no use.
        if not ("cloud" in model["capabilities"] and not model["sizes"])
    ]
    return result


@app.get("/models/catalog/{name}")
def model_catalog_tags(name: str):
    """The variants of one model, their size, and whether they fit in this computer's graphics card."""
    try:
        result = ollama_catalog.model_tags(_catalog_redis(), name)
    except ollama_catalog.CatalogError as exc:
        raise HTTPException(404 if "introuvable" in str(exc) else 503, str(exc)) from exc
    gpu = _redis_json(GPU_KEY) or {}
    total = (gpu.get("memory_total_mb") or 0) * 1024 ** 2
    installed = _installed_names()
    tags = []
    for tag in result["tags"]:
        size = tag.get("size_bytes")
        needed = int(size * VRAM_PER_FILE_BYTE + VRAM_CONTEXT_BYTES) if size else None
        tags.append(tag | {
            "installed": tag["name"] in installed or (tag["name"].endswith(":latest") and tag["name"][:-7] in installed),
            "vram_bytes": needed,
            "fits": None if not needed or not total else needed <= total - VRAM_RESERVED_BYTES,
        })
    return result | {"tags": tags, "gpu_memory_bytes": total or None}


@app.post("/models/llm/pull")
async def pull_model(payload: ModelPullIn):
    """Download a model into Ollama, as Server-Sent Events: `progress` {status, completed, total}, then `done` or `error`.

    The name is checked against Ollama's registry first: a model withdrawn or
    mistyped is refused at once, instead of a download that fails.
    """
    found = await run_in_threadpool(ollama_catalog.exists, payload.name)
    if found is False:
        raise HTTPException(422, f"« {payload.name} » n'existe pas dans le catalogue d'Ollama (retiré, ou nom mal saisi)")

    async def events():
        try:
            timeout = httpx.Timeout(None, connect=10)
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", f"{settings.ollama_url.rstrip('/')}/api/pull", json={"model": payload.name, "stream": True}) as response:
                    if response.status_code >= 400:
                        await response.aread()
                        detail = (response.json() or {}).get("error") if response.content else None
                        yield _sse("error", {"detail": f"Téléchargement refusé : {detail or response.status_code}"})
                        return
                    async for line in response.aiter_lines():
                        if not line.strip():
                            continue
                        chunk = json.loads(line)
                        if chunk.get("error"):
                            yield _sse("error", {"detail": f"Téléchargement échoué : {chunk['error']}"})
                            return
                        yield _sse("progress", {"status": chunk.get("status"), "completed": chunk.get("completed"), "total": chunk.get("total")})
                        if chunk.get("status") == "success":
                            yield _sse("done", {"name": payload.name})
                            return
        except httpx.HTTPError:
            logger.warning("Model pull of %s failed", payload.name, exc_info=True)
            yield _sse("error", {"detail": "Ollama ne répond pas"})

    return StreamingResponse(events(), media_type="text/event-stream", headers=SSE_HEADERS)


@app.delete("/models/llm/{name:path}")
def delete_model(name: str):
    if _same_model(name, ai_models.llm_model()) or _same_model(name, settings.llm_model):
        raise HTTPException(409, "Ce modèle est utilisé : choisissez-en un autre avant de le supprimer")
    if _same_model(name, settings.embedding_model):
        raise HTTPException(409, "Ce modèle sert à la recherche par le sens : il ne peut pas être supprimé")
    try:
        _ollama("DELETE", "/api/delete", json={"model": name})
    except httpx.HTTPStatusError as exc:
        raise HTTPException(404, "Modèle introuvable") from exc
    except Exception as exc:
        raise HTTPException(503, "Ollama ne répond pas") from exc
    return {"deleted": True}


def _llm_benchmark(name: str) -> dict:
    body = _ollama("POST", "/api/generate", timeout=settings.llm_timeout_seconds, json={
        "model": name, "prompt": BENCHMARK_PROMPT, "stream": False, "think": False,
        "options": {"temperature": 0.1, "num_predict": 200, "num_ctx": settings.llm_num_ctx},
    })
    seconds = lambda value: (value or 0) / 1e9  # noqa: E731  (Ollama durations are in nanoseconds)
    generation = seconds(body.get("eval_duration"))
    reading = seconds(body.get("prompt_eval_duration"))
    return {
        "model": name,
        "load_seconds": round(seconds(body.get("load_duration")), 2),
        "tokens_per_second": round(body.get("eval_count", 0) / generation, 1) if generation else None,
        "prompt_tokens_per_second": round(body.get("prompt_eval_count", 0) / reading, 1) if reading else None,
        "total_seconds": round(seconds(body.get("total_duration")), 2),
        "answer": (body.get("response") or "").strip()[:600],
    }


@app.post("/models/llm/benchmark")
async def benchmark_llm(payload: BenchmarkIn):
    """Load the model and write a short summary: load time and speed (tokens per second)."""
    try:
        return await run_in_threadpool(_llm_benchmark, payload.name)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(422, "Ce modèle n'est pas installé") from exc
    except Exception as exc:
        logger.warning("LLM benchmark of %s failed", payload.name, exc_info=True)
        raise HTTPException(503, "Test impossible : Ollama ne répond pas") from exc


def _whisper_sample() -> tuple[str, str] | None:
    """The most recent media that can be listened to (path, title): the test transcribes its first minute."""
    with SessionLocal() as db:
        for video in db.scalars(select(Video).where(Video.status == "COMPLETED").order_by(desc(Video.created_at)).limit(50)):
            for path in (_audio_path(video.id), Path(video.path)):
                if path.is_file():
                    return str(path), video.original_filename
    return None


@app.post("/models/whisper/benchmark")
def benchmark_whisper(payload: BenchmarkIn):
    """Ask the live service (it has the GPU) to time a Whisper model on the first minute of a video."""
    if payload.name not in ai_models.WHISPER_NAMES:
        raise HTTPException(422, "Modèle de transcription inconnu")
    sample = _whisper_sample()
    if sample is None:
        raise HTTPException(409, "Importez d'abord une vidéo : le test transcrit sa première minute")
    request_id = str(uuid.uuid4())
    request = {"id": request_id, "model": payload.name, "path": sample[0], "title": sample[1], "seconds": WHISPER_SAMPLE_SECONDS}
    try:
        redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        redis.set(f"{BENCHMARK_KEY}{request_id}", json.dumps({"status": "pending", "model": payload.name}), ex=3600)
        redis.rpush(BENCHMARK_QUEUE, json.dumps(request))
    except Exception as exc:
        raise HTTPException(503, "Service indisponible (Redis)") from exc
    return {"id": request_id, "status": "pending", "sample": sample[1]}


@app.get("/models/whisper/benchmark/{request_id}")
def whisper_benchmark_result(request_id: str):
    result = _redis_json(f"{BENCHMARK_KEY}{request_id}")
    if result is None:
        raise HTTPException(404, "Test introuvable")
    return result


# The newest domains live in app/routes/ (one router each). Imported last: they
# use helpers defined above (_video_or_404, cancel_job…).
from .routes import access as access_routes  # noqa: E402
from .routes import actions as actions_routes  # noqa: E402
from .routes import clips as clips_routes  # noqa: E402
from .routes import quality as quality_routes  # noqa: E402
from .routes import series as series_routes  # noqa: E402

for _module in (actions_routes, clips_routes, series_routes, quality_routes, access_routes):
    app.include_router(_module.router)
