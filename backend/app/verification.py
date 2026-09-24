"""A summary one can check (n°3): each line points to the passage it comes from.

Every line of the summary that states something (a bullet, a sentence) is
embedded and compared with the video's passages, the same ones the questions
use. The closest passage is its source; a line with no passage close enough
is flagged « à vérifier »: the model may have extrapolated. A timestamp the
model wrote itself is kept, and checked against the passage found.

Computed on demand and cached with the summary: the cache is valid for this
text of the summary and this state of the index.
"""
import hashlib
import json
import re
from dataclasses import asdict, dataclass

from sqlalchemy.orm import Session

from .models import Summary, VideoIndex
from .retrieval import embed_texts, search

# Beyond this cosine distance (bge-m3), no passage is about what the line says.
# Measured on a laptop review (2026-09-24): its 10 summary lines 0.31-0.38;
# statements added by hand 0.43 (a laptop brand never cited) to 0.60 (off-topic).
# What it cannot see: a wrong detail on a subject that is discussed (« le Katana
# a un écran OLED », false, 0.38). The source passage is there to be read.
SUPPORT_MAX_DISTANCE = 0.40
# « Budget marketing validé »: three words are a statement.
MIN_WORDS = 3
EXCERPT_CHARS = 280
_CLOCK = re.compile(r"\[(\d{1,2}):(\d{2})(?::(\d{2}))?\]")
_MARKUP = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+|[*_`#>]")
_EMPTY = re.compile(r"^(non mentionn|aucune? |pas de |none|not mentioned)", re.IGNORECASE)


@dataclass
class LineSource:
    line: int
    start_seconds: float
    end_seconds: float
    distance: float
    supported: bool
    excerpt: str
    # The timestamp the summary gives itself, if any, and whether the passage found is there.
    cited_seconds: float | None = None
    cited_matches: bool | None = None


def statement_lines(markdown: str) -> list[tuple[int, str]]:
    """(index, plain text) of the lines that state something: no heading, no « Non mentionné »."""
    found = []
    for index, raw in enumerate(markdown.splitlines()):
        if raw.lstrip().startswith("#"):
            continue
        text = " ".join(_MARKUP.sub(" ", _CLOCK.sub(" ", raw)).split())
        if len(text.split()) < MIN_WORDS or _EMPTY.match(text):
            continue
        found.append((index, text))
    return found


def cited_clock(line: str) -> float | None:
    match = _CLOCK.search(line)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    if seconds is None:
        hours, minutes, seconds = "0", hours, minutes
    return float(int(hours) * 3600 + int(minutes) * 60 + int(seconds))


def cache_key(summary: Summary, index: VideoIndex) -> str:
    digest = hashlib.sha256(summary.content_markdown.encode("utf-8")).hexdigest()
    return f"{digest}|{index.model}|{index.transcript_hash}"


def link_sources(db: Session, summary: Summary) -> dict:
    """{"status": "ready", "lines": [...]}, or "indexing" while the passages are not ready."""
    index = db.get(VideoIndex, summary.video_id)
    if index is None or index.status != "READY":
        return {"status": "indexing", "lines": []}
    key = cache_key(summary, index)
    if summary.sources:
        cached = json.loads(summary.sources)
        if cached.get("key") == key:
            return {"status": "ready", "lines": cached["lines"]}
    raw_lines = summary.content_markdown.splitlines()
    statements = statement_lines(summary.content_markdown)
    vectors = embed_texts([text for _, text in statements]) if statements else []
    lines = []
    for (position, _), vector in zip(statements, vectors):
        hits = search(db, [summary.video_id], vector, limit=1)
        if not hits:
            continue
        hit = hits[0]
        excerpt = " ".join(_CLOCK.sub(" ", hit.text).split())
        cited = cited_clock(raw_lines[position])
        lines.append(asdict(LineSource(
            line=position, start_seconds=hit.start_seconds, end_seconds=hit.end_seconds, distance=round(hit.distance, 3),
            supported=hit.distance <= SUPPORT_MAX_DISTANCE,
            excerpt=excerpt[:EXCERPT_CHARS] + ("…" if len(excerpt) > EXCERPT_CHARS else ""),
            cited_seconds=cited,
            # A cited time within the passage found (a minute of margin): the model pointed at the right place.
            cited_matches=None if cited is None else hit.start_seconds - 60 <= cited <= hit.end_seconds + 60,
        )))
    summary.sources = json.dumps({"key": key, "lines": lines}, ensure_ascii=False)
    return {"status": "ready", "lines": lines}
