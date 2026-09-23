"""Semantic search over transcripts (n°6 chat on a whole video, n°19 library questions).

A transcript is cut into passages of about a minute, at segment boundaries,
each embedded with a multilingual model served by Ollama. A question is
embedded the same way and compared with every passage of the videos in scope
(exact cosine search with pgvector: a library is tens of thousands of rows).
"""
import hashlib
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import settings
from .models import Passage, VideoIndex
from .utils import timestamp

logger = logging.getLogger(__name__)

# ~900 characters is 150-200 words: one idea, precise enough to rank well, and
# long enough to carry its context. A passage never spans more than 2 minutes.
PASSAGE_TARGET_CHARS = 900
PASSAGE_MAX_SECONDS = 120
EMBED_BATCH_SIZE = 32


def transcript_hash(transcript: str | None) -> str:
    return hashlib.sha256((transcript or "").encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class PassageDraft:
    start_seconds: float
    end_seconds: float
    # "[hh:mm:ss] text" lines, for the LLM (it cites these timestamps).
    text: str
    # Plain words, for the embedding: timestamps would only add noise.
    plain: str


def build_passages(segments: Iterable[tuple[float, float, str]]) -> list[PassageDraft]:
    """Group consecutive segments; the last segment of a passage opens the next one.

    The overlap keeps an answer that straddles two passages findable from either.
    """
    rows = [(start, end, text.strip()) for start, end, text in segments if text and text.strip()]
    passages: list[PassageDraft] = []
    index = 0
    while index < len(rows):
        group = [rows[index]]
        size = len(rows[index][2])
        next_index = index + 1
        while next_index < len(rows):
            start, end, text = rows[next_index]
            if size + len(text) > PASSAGE_TARGET_CHARS or end - group[0][0] > PASSAGE_MAX_SECONDS:
                break
            group.append(rows[next_index])
            size += len(text) + 1
            next_index += 1
        passages.append(PassageDraft(
            start_seconds=group[0][0],
            end_seconds=group[-1][1],
            text="\n".join(f"[{timestamp(start)}] {text}" for start, _, text in group),
            plain=" ".join(text for _, _, text in group),
        ))
        if next_index >= len(rows):
            break
        # Overlap by one segment, unless the passage is that single segment.
        index = next_index - 1 if next_index - 1 > index else next_index
    return passages


def embed_texts(texts: list[str], on_batch: Callable[[int, int], None] | None = None) -> list[list[float]]:
    """Embeddings from Ollama's /api/embed, in batches."""
    vectors: list[list[float]] = []
    with httpx.Client(timeout=settings.llm_timeout_seconds) as client:
        for offset in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[offset:offset + EMBED_BATCH_SIZE]
            response = client.post(
                f"{settings.ollama_url}/api/embed",
                json={"model": settings.embedding_model, "input": batch, "truncate": True},
            )
            response.raise_for_status()
            embeddings = response.json().get("embeddings") or []
            if len(embeddings) != len(batch):
                raise RuntimeError(f"Ollama returned {len(embeddings)} embeddings for {len(batch)} texts")
            vectors.extend(embeddings)
            if on_batch:
                on_batch(min(offset + EMBED_BATCH_SIZE, len(texts)), len(texts))
    return vectors


def write_index(db: Session, video_id: str, drafts: list[PassageDraft], vectors: list[list[float]], source_hash: str) -> None:
    """Replace the video's passages and mark its index READY (caller commits)."""
    db.execute(delete(Passage).where(Passage.video_id == video_id))
    db.add_all([
        Passage(
            video_id=video_id, position=position, start_seconds=draft.start_seconds,
            end_seconds=draft.end_seconds, text=draft.text, embedding=vector,
        )
        for position, (draft, vector) in enumerate(zip(drafts, vectors))
    ])
    state = db.get(VideoIndex, video_id) or VideoIndex(video_id=video_id)
    state.status = "READY"
    state.model = settings.embedding_model
    state.transcript_hash = source_hash
    state.passages = len(drafts)
    state.error = None
    state.updated_at = datetime.now(timezone.utc)
    db.merge(state)


def record_index_failure(db: Session, video_id: str, source_hash: str, error: str) -> None:
    state = db.get(VideoIndex, video_id) or VideoIndex(video_id=video_id, passages=0)
    state.status = "FAILED"
    state.model = settings.embedding_model
    state.transcript_hash = source_hash
    state.error = error
    state.updated_at = datetime.now(timezone.utc)
    db.merge(state)


def ready_video_ids(db: Session, video_ids: list[str]) -> set[str]:
    """Videos whose passages match their current transcript and the current model."""
    if not video_ids:
        return set()
    return set(db.scalars(
        select(VideoIndex.video_id).where(
            VideoIndex.video_id.in_(video_ids),
            VideoIndex.status == "READY",
            VideoIndex.model == settings.embedding_model,
        )
    ))


@dataclass(frozen=True)
class Hit:
    video_id: str
    start_seconds: float
    end_seconds: float
    text: str
    distance: float


def search(db: Session, video_ids: list[str], query: list[float], *, limit: int, per_video: int | None = None) -> list[Hit]:
    """Closest passages first; at most `per_video` from one video when several are searched."""
    if not video_ids:
        return []
    fetch = limit * 4 if per_video else limit
    if db.get_bind().dialect.name == "postgresql":
        distance = Passage.embedding.cosine_distance(query)
        rows = db.execute(
            select(Passage.video_id, Passage.start_seconds, Passage.end_seconds, Passage.text, distance.label("distance"))
            .where(Passage.video_id.in_(video_ids))
            .order_by(distance)
            .limit(fetch)
        ).all()
        hits = [Hit(row.video_id, row.start_seconds, row.end_seconds, row.text, float(row.distance)) for row in rows]
    else:
        # SQLite (unit tests): the same exact search, in numpy.
        rows = db.execute(
            select(Passage.video_id, Passage.start_seconds, Passage.end_seconds, Passage.text, Passage.embedding)
            .where(Passage.video_id.in_(video_ids))
        ).all()
        if not rows:
            return []
        matrix = np.array([np.asarray(row.embedding, dtype=np.float32) for row in rows])
        wanted = np.asarray(query, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1) * np.linalg.norm(wanted)
        distances = 1 - (matrix @ wanted) / np.where(norms == 0, 1, norms)
        order = np.argsort(distances, kind="stable")[:fetch]
        hits = [Hit(rows[i].video_id, rows[i].start_seconds, rows[i].end_seconds, rows[i].text, float(distances[i])) for i in order]
    if per_video:
        counts: dict[str, int] = {}
        kept = []
        for hit in hits:
            if counts.get(hit.video_id, 0) < per_video:
                kept.append(hit)
                counts[hit.video_id] = counts.get(hit.video_id, 0) + 1
        hits = kept
    return hits[:limit]


def merge_hits(hits: list[Hit]) -> list[Hit]:
    """Chronological order; overlapping passages of one video become one extract."""
    merged: list[Hit] = []
    for hit in sorted(hits, key=lambda item: (item.video_id, item.start_seconds)):
        last = merged[-1] if merged else None
        if last and last.video_id == hit.video_id and hit.start_seconds <= last.end_seconds:
            known = set(last.text.splitlines())
            extra = [line for line in hit.text.splitlines() if line not in known]
            merged[-1] = Hit(
                last.video_id, last.start_seconds, max(last.end_seconds, hit.end_seconds),
                "\n".join([last.text, *extra]), min(last.distance, hit.distance),
            )
        else:
            merged.append(hit)
    return merged


def retrieval_query(question: str, previous_questions: list[str]) -> str:
    """A short follow-up ("et ensuite ?") is searched together with the previous question."""
    if previous_questions and len(question.split()) < 6:
        return f"{previous_questions[-1]}\n{question}"
    return question
