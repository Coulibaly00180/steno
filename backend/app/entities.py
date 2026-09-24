"""People, organisations, places and dates of the library (n°16).

The LLM reads each block of a transcript and names what it finds, with the
time of its first appearance in the block. Times are checked against the
block (the model may invent one), and each mention keeps the line said at
that moment. The same name met in two videos is one entity: names are folded
(accents, case, titles such as « M. » or « Dr »), and the user can rename,
merge or hide what the folding missed.

Runs in the background (INDEX queue, after any analysis): a 6-hour video is
~35 LLM calls, and the rest of the product never waits for it.
"""
import logging
import re
import unicodedata
from bisect import bisect_right
from dataclasses import dataclass

from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from . import ai_models
from .config import settings
from .models import Entity, EntityMention, TranscriptSegment, VideoEntityState, utcnow
from .utils import split_text

logger = logging.getLogger(__name__)

KINDS = ("person", "organization", "place", "date")
KIND_LABELS = {"person": "Personne", "organization": "Organisation", "place": "Lieu", "date": "Date"}
NAME_MAX_CHARS = 120
CONTEXT_MAX_CHARS = 400
# Titles dropped from a person's key: « M. Dupont » and « Dupont » meet.
_TITLES = re.compile(r"^(m|mme|mlle|mr|mrs|ms|dr|pr|me|monsieur|madame|mademoiselle|docteur|professeur|maitre)\.?\s+")
_CLOCK = re.compile(r"^\[?(\d{1,2}):(\d{2})(?::(\d{2}))?\]?$")
# Measured on a laptop review (2026-09-24): qwen3:8b returned prices, frame rates
# and screen sizes as dates or places (« 1200 euros », « 144 hertz »), and the
# diarization labels as people. A date must look like one; the rest must not
# start with a number.
_DATE_WORDS = re.compile(
    r"\b(janv|fevr|mars|avr|mai|juin|juil|aout|sept|oct|nov|dec|lundi|mardi|mercredi|jeudi|vendredi|samedi|dimanche"
    r"|jan|feb|apr|jun|jul|aug|monday|tuesday|wednesday|thursday|friday|saturday|sunday|january|february|march|april|may|june"
    r"|july|august|september|october|november|december|demain|hier|aujourd|semaine|mois|trimestre|semestre|annee|an|ans"
    r"|week|month|quarter|year|today|tomorrow|yesterday|noel|paques|toussaint|rentree|ete|hiver|printemps|automne|echeance)",
)
_YEAR_OR_DAY = re.compile(r"\b(19|20)\d{2}\b|\b\d{1,2}[/.-]\d{1,2}([/.-]\d{2,4})?\b|\b(1er|premier)\b")
_UNITS = re.compile(r"\b(euros?|eur|dollars?|%|pour ?cent|fps|hertz|hz|pouces?|watts?|go|mo|to|gb|mb|km|kg|cm|mm|ghz|mhz|volts?)\b|[€$%]")
_SPEAKER_LABEL = re.compile(r"^(intervenant|speaker|locuteur)\s*\d+$")


def plausible(kind: str, name: str) -> bool:
    """Whether a name can be an entity of this kind, whatever the model said."""
    text = normalize(name)
    if _SPEAKER_LABEL.match(text):
        return False
    if kind == "date":
        return not _UNITS.search(f" {name.lower()} ") and bool(_DATE_WORDS.search(text) or _YEAR_OR_DAY.search(name.lower()))
    return not text[:1].isdigit() and not _UNITS.search(f" {name.lower()} ")
_LINE_CLOCK = re.compile(r"\[(\d{1,2}):(\d{2}):(\d{2})\]")


def normalize(text: str) -> str:
    """No accents, no case, no punctuation, single spaces: how names are compared."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(char for char in text if not unicodedata.combining(char)).casefold()
    text = re.sub(r"[^\w\s-]", " ", text.replace("'", " ").replace("’", " "))
    return " ".join(text.split())


def fold(name: str) -> str:
    return normalize(name)[:NAME_MAX_CHARS]


def entity_key(kind: str, name: str) -> str:
    key = fold(name)
    if kind == "person":
        key = _TITLES.sub("", key)
    return key


def clean_name(name: str) -> str:
    return " ".join(str(name).split()).strip(" .,;:-–«»\"'")[:NAME_MAX_CHARS]


def parse_moment(value: str) -> float | None:
    match = _CLOCK.match(str(value).strip())
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    if seconds is None:  # "mm:ss"
        hours, minutes, seconds = "0", hours, minutes
    if int(minutes) >= 60 or int(seconds) >= 60:
        return None
    return float(int(hours) * 3600 + int(minutes) * 60 + int(seconds))


def block_range(text: str) -> tuple[float, float] | None:
    clocks = [int(h) * 3600 + int(m) * 60 + int(s) for h, m, s in _LINE_CLOCK.findall(text)]
    return (float(min(clocks)), float(max(clocks))) if clocks else None


@dataclass(frozen=True)
class Found:
    kind: str
    name: str
    key: str
    start_seconds: float


def validate(items: list[dict], block: str) -> list[Found]:
    """Keep the items with a known type, a real name and a time inside the block."""
    from .llm import ENTITY_TYPES

    time_range = block_range(block)
    if time_range is None:
        return []
    low, high = time_range
    # Padded with spaces: "Lyon" must match the word, not the middle of "Lyonnaise".
    words = f" {normalize(block)} "
    found: dict[tuple[str, str], Found] = {}
    for item in items:
        kind = ENTITY_TYPES.get(str(item.get("type", "")).strip().lower())
        name = clean_name(item.get("nom", ""))
        start = parse_moment(item.get("moment", ""))
        if kind is None or start is None or not (low - 1 <= start <= high + 1):
            continue
        key = entity_key(kind, name)
        if len(key) < 2 or not plausible(kind, name):
            continue
        # The name must be in the block: a model sometimes "finds" what it knows.
        if kind != "date" and f" {normalize(name)} " not in words:
            continue
        found.setdefault((kind, key), Found(kind, name, key, start))
    return list(found.values())


# The model's time is often a line or two off (a laptop review: « Amazon » placed on a
# line that does not name it): the line naming the entity is looked for this close.
LOCATE_WINDOW_SECONDS = 30.0


def context_at(starts: list[float], texts: list[str], seconds: float) -> str:
    """The transcript line said at `seconds`."""
    index = max(0, bisect_right(starts, seconds + 0.5) - 1)
    return texts[index][:CONTEXT_MAX_CHARS] if texts else ""


def locate(starts: list[float], texts: list[str], seconds: float, name: str) -> tuple[float, str]:
    """The moment and line naming `name`, nearest to `seconds`; else the line at `seconds`."""
    wanted = f" {normalize(name)} "
    low = bisect_right(starts, seconds - LOCATE_WINDOW_SECONDS)
    high = bisect_right(starts, seconds + LOCATE_WINDOW_SECONDS)
    naming = [index for index in range(low, high) if wanted in f" {normalize(texts[index])} "]
    if naming:
        index = min(naming, key=lambda position: abs(starts[position] - seconds))
        return starts[index], texts[index][:CONTEXT_MAX_CHARS]
    return seconds, context_at(starts, texts, seconds)


def extract(transcript: str, *, vocabulary: list[str], on_block=None) -> list[Found]:
    """Every entity of a transcript, block by block (LLM calls, outside any transaction)."""
    from .llm import extract_entities

    found: list[Found] = []
    blocks = split_text(transcript, settings.summary_chunk_chars)
    for index, block in enumerate(blocks, 1):
        found.extend(validate(extract_entities(block, vocabulary=vocabulary), block))
        if on_block:
            on_block(index, len(blocks))
    return found


MERGE_CHAIN_MAX = 10


def _entity(db: Session, found: Found) -> Entity:
    """The entity of this name; a merged one leads to the entity it was merged into."""
    entity = db.scalar(select(Entity).where(Entity.kind == found.kind, Entity.key == found.key))
    if entity is None:
        entity = Entity(kind=found.kind, name=found.name, key=found.key)
        db.add(entity)
        db.flush()
        return entity
    for _ in range(MERGE_CHAIN_MAX):
        if entity.merged_into is None:
            break
        target = db.get(Entity, entity.merged_into)
        if target is None:
            break
        entity = target
    return entity


def write(db: Session, video_id: str, found: list[Found], source_hash: str) -> int:
    """Replace the video's mentions; returns how many. The caller commits."""
    db.execute(delete(EntityMention).where(EntityMention.video_id == video_id))
    segments = db.execute(
        select(TranscriptSegment.start_seconds, TranscriptSegment.text)
        .where(TranscriptSegment.video_id == video_id)
        .order_by(TranscriptSegment.start_seconds, TranscriptSegment.id)
    ).all()
    starts, texts = [row.start_seconds for row in segments], [row.text for row in segments]
    written: set[tuple[int, float]] = set()
    for item in found:
        entity_id = _entity(db, item).id
        moment, context = locate(starts, texts, item.start_seconds, item.name) if item.kind != "date" else (
            item.start_seconds, context_at(starts, texts, item.start_seconds)
        )
        # Two spellings merged into one entity, named at the same moment: one mention.
        if (entity_id, moment) in written:
            continue
        written.add((entity_id, moment))
        db.add(EntityMention(entity_id=entity_id, video_id=video_id, start_seconds=moment, context=context))
    db.flush()
    delete_orphans(db)
    state = db.get(VideoEntityState, video_id) or VideoEntityState(video_id=video_id)
    state.status, state.model, state.transcript_hash = "READY", ai_models.llm_model(), source_hash
    state.mentions, state.error, state.updated_at = len(written), None, utcnow()
    db.merge(state)
    return len(written)


def record_failure(db: Session, video_id: str, source_hash: str, error: str) -> None:
    state = db.get(VideoEntityState, video_id) or VideoEntityState(video_id=video_id, mentions=0)
    state.status, state.model, state.transcript_hash = "FAILED", ai_models.llm_model(), source_hash
    state.error, state.updated_at = error[:500], utcnow()
    db.merge(state)


def delete_orphans(db: Session) -> None:
    """Entities no video names any more; hidden ones are kept (the user dismissed them)."""
    db.execute(delete(Entity).where(
        Entity.hidden.is_(False), ~exists().where(EntityMention.entity_id == Entity.id)
    ))


def merge(db: Session, source: Entity, target: Entity) -> None:
    """`source` becomes `target`: its mentions move over (one per moment of a video)."""
    known = set(db.execute(
        select(EntityMention.video_id, EntityMention.start_seconds).where(EntityMention.entity_id == target.id)
    ).all())
    for mention in db.scalars(select(EntityMention).where(EntityMention.entity_id == source.id)):
        if (mention.video_id, mention.start_seconds) in known:
            db.delete(mention)
        else:
            mention.entity_id = target.id
    # Kept, hidden, pointing at the target: a new extraction sends this spelling's mentions there.
    source.hidden = True
    source.merged_into = target.id
    for earlier in db.scalars(select(Entity).where(Entity.merged_into == source.id)):
        earlier.merged_into = target.id
    db.flush()


def counts(db: Session, *, kind: str | None = None, query: str | None = None, include_hidden: bool = False, limit: int = 500):
    """Entities with their number of videos and mentions, most widespread first."""
    videos = func.count(func.distinct(EntityMention.video_id))
    statement = (
        select(Entity, videos.label("videos"), func.count(EntityMention.id).label("mentions"))
        .join(EntityMention, EntityMention.entity_id == Entity.id)
        .group_by(Entity.id)
        .order_by(videos.desc(), func.count(EntityMention.id).desc(), Entity.name)
        .limit(limit)
    )
    if kind:
        statement = statement.where(Entity.kind == kind)
    if not include_hidden:
        statement = statement.where(Entity.hidden.is_(False))
    if query and fold(query):
        statement = statement.where(Entity.key.contains(fold(query), autoescape=True))
    return db.execute(statement).all()
