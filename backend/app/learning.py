"""Glossary that learns from corrections (n°2).

A hand correction of the transcript ("d'Oriana" → "Doñana") is recorded when
the new words look like a term: a proper noun, an acronym, a product name,
i.e. a word with a capital letter or a digit. Ordinary fixes ("vert" → "verre")
are left out. A term corrected at least SUGGESTION_MIN_OCCURRENCES times, not
in the glossary yet and never declined, is suggested for the global glossary.
"""
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from .analysis_options import TERM_MAX_CHARS
from .models import GlossaryDismissal, GlossaryTerm, TermCorrection

SUGGESTION_MIN_OCCURRENCES = 2
# A longer span is a rewritten sentence, not a misheard name.
TERM_MAX_WORDS = 4
MISHEARD_MAX_WORDS = 6
MISHEARD_MAX_CHARS = 120
SUGGESTIONS_LIMIT = 20
VARIANTS_SHOWN = 5
# Corrections read to build the suggestions: the most recent ones.
CORRECTIONS_WINDOW = 5000
_EDGE = " \t.,;:!?…\"'«»“”‘’()[]{}"


def _strip(word: str) -> str:
    return word.strip(_EDGE)


def is_term_like(word: str) -> bool:
    return any(char.isupper() or char.isdigit() for char in word)


def core_term(words: list[str]) -> str | None:
    """The term within replacement words: lower-case words at either end are dropped.

    "de Doñana" → "Doñana"; "Parc national de Doñana" is kept whole.
    """
    kept = [word for word in (_strip(word) for word in words) if word]
    while kept and not is_term_like(kept[0]):
        kept.pop(0)
    while kept and not is_term_like(kept[-1]):
        kept.pop()
    if not kept or len(kept) > TERM_MAX_WORDS:
        return None
    term = " ".join(kept)
    return term if 2 <= len(term) <= TERM_MAX_CHARS else None


def _pair(misheard_words: list[str], replacement_words: list[str]) -> tuple[str, str] | None:
    if len(misheard_words) > MISHEARD_MAX_WORDS:
        return None
    term = core_term(replacement_words)
    misheard = " ".join(word for word in (_strip(word) for word in misheard_words) if word)
    if not term or not misheard or len(misheard) > MISHEARD_MAX_CHARS or misheard == term:
        return None
    # The term was already there, spelt the same: the edit changed its surroundings.
    if f" {term} " in f" {misheard} ":
        return None
    return misheard, term


def corrections_between(old: str, new: str) -> list[tuple[str, str]]:
    """(misheard, term) pairs of a line edit, from a word-level diff."""
    before, after = old.split(), new.split()
    pairs = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=before, b=after, autojunk=False).get_opcodes():
        if tag == "replace":
            pair = _pair(before[i1:i2], after[j1:j2])
            if pair:
                pairs.append(pair)
    return pairs


def replacement_pair(find: str, replace: str) -> tuple[str, str] | None:
    """(misheard, term) of a « Tout remplacer »."""
    return _pair(find.split(), replace.split())


def record_corrections(db: Session, video_id: str | None, pairs: list[tuple[str, str, int]]) -> None:
    """Add (misheard, term, occurrences) rows to the session; the caller commits."""
    db.add_all([
        TermCorrection(video_id=video_id, misheard=misheard, term=term, occurrences=occurrences)
        for misheard, term, occurrences in pairs
        if occurrences > 0
    ])


@dataclass
class Suggestion:
    term: str
    variants: list[str]
    occurrences: int
    videos: int


def suggestions(db: Session, limit: int = SUGGESTIONS_LIMIT) -> list[Suggestion]:
    glossary = {term.casefold() for term in db.scalars(select(GlossaryTerm.term))}
    dismissed = {term.casefold() for term in db.scalars(select(GlossaryDismissal.term))}
    rows = db.execute(
        select(TermCorrection.term, TermCorrection.misheard, TermCorrection.occurrences, TermCorrection.video_id)
        .order_by(desc(TermCorrection.id))
        .limit(CORRECTIONS_WINDOW)
    ).all()
    spellings: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    variants: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    videos: dict[str, set[str]] = defaultdict(set)
    latest: dict[str, int] = {}
    for rank, (term, misheard, occurrences, video_id) in enumerate(rows):
        key = term.casefold()
        if key in glossary or key in dismissed:
            continue
        spellings[key][term] += occurrences
        variants[key][misheard] += occurrences
        if video_id:
            videos[key].add(video_id)
        latest.setdefault(key, rank)
    found = []
    for key, by_spelling in spellings.items():
        total = sum(by_spelling.values())
        if total < SUGGESTION_MIN_OCCURRENCES:
            continue
        # The spelling typed most often wins.
        term = max(by_spelling.items(), key=lambda item: item[1])[0]
        shown = sorted(variants[key].items(), key=lambda item: -item[1])[:VARIANTS_SHOWN]
        found.append((total, -latest[key], Suggestion(term, [name for name, _ in shown], total, len(videos[key]))))
    found.sort(key=lambda item: (-item[0], -item[1]))
    return [suggestion for *_, suggestion in found[:limit]]


def dismiss(db: Session, term: str) -> None:
    exists = db.scalar(select(GlossaryDismissal.id).where(func.lower(GlossaryDismissal.term) == term.lower()))
    if exists is None:
        db.add(GlossaryDismissal(term=term))
