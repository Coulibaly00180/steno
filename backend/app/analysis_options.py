"""Business rules for analysis options (docs/specs/phase-1-resultats-justes.md).

Pure functions only: shared by the API (validation, display counts) and the
worker (prompts), and unit-tested without a database.
"""
import re

# --- Summary length (R-1) ---------------------------------------------------

SUMMARY_LENGTHS = ("short", "standard", "detailed")
DEFAULT_SUMMARY_LENGTH = "standard"
SUMMARY_LENGTH_LABELS = {"short": "court", "standard": "standard", "detailed": "détaillé"}

# Provisional coefficients, to be calibrated on the reference corpus (§19).
# Short, partially calibrated (2026-09-23): with the five sections of the
# default template, qwen3:8b writes ~200 words whatever the budget; 150 was cut.
SHORT_BASE, SHORT_PER_MINUTE, SHORT_MIN, SHORT_MAX = 200, 2.5, 200, 450
STANDARD_BASE, STANDARD_PER_MINUTE, STANDARD_FREE_MINUTES, STANDARD_MIN, STANDARD_MAX = 250, 8, 15, 250, 1500
DETAILED_FACTOR, DETAILED_MIN, DETAILED_MAX = 2, 500, 2500

# Measured with qwen3:8b (Ollama eval_count, 2026-09-23): 1.73 tokens per word
# for a French summary, 1.40 in English, 2.28 for short bullets full of
# [hh:mm:ss] timestamps. 1.6 used to cut French summaries before their last
# sections. The fixed part covers headings and timestamps of short summaries.
TOKENS_PER_WORD = 2.0
FINAL_OUTPUT_OVERHEAD_TOKENS = 150
# Rough size estimate used to decide the hierarchical reduction (F-5.5).
CHARS_PER_TOKEN = 3.5


def _clamp(value: float, low: int, high: int) -> int:
    return int(max(low, min(high, value)))


def word_budget(duration_seconds: float, summary_length: str) -> int:
    minutes = max(0.0, duration_seconds) / 60
    standard = _clamp(
        STANDARD_BASE + STANDARD_PER_MINUTE * max(0.0, minutes - STANDARD_FREE_MINUTES),
        STANDARD_MIN,
        STANDARD_MAX,
    )
    if summary_length == "short":
        return _clamp(SHORT_BASE + SHORT_PER_MINUTE * minutes, SHORT_MIN, SHORT_MAX)
    if summary_length == "detailed":
        return _clamp(DETAILED_FACTOR * standard, DETAILED_MIN, DETAILED_MAX)
    return standard


def final_output_tokens(word_budget: int) -> int:
    """num_predict of the final summary: a hard safety cap, above what the model needs."""
    return int(word_budget * TOKENS_PER_WORD) + FINAL_OUTPUT_OVERHEAD_TOKENS


def estimated_tokens(text: str) -> int:
    return int(len(text) / CHARS_PER_TOKEN) + 1


# --- Languages (F-11.1, F-7.2) ------------------------------------------------

SOURCE_LANGUAGES = {
    "fr": "français",
    "en": "anglais",
    "es": "espagnol",
    "de": "allemand",
    "it": "italien",
    "pt": "portugais",
    "nl": "néerlandais",
    "ar": "arabe",
    "zh": "chinois",
    "ja": "japonais",
}


def language_name(code_or_name: str | None) -> str | None:
    """ISO code (Whisper) or French name (translation target) → French name."""
    if not code_or_name:
        return None
    return SOURCE_LANGUAGES.get(code_or_name.lower(), code_or_name)


# --- Vocabulary and glossary (F-11.3, F-11.10, F-11.12, R-9) --------------------

VOCABULARY_MAX_CHARS = 1000
VOCABULARY_MAX_TERMS = 100
GLOSSARY_MAX_TERMS = 300
TERM_MAX_CHARS = 60
WHISPER_VOCABULARY_MAX_CHARS = 800
LLM_VOCABULARY_MAX_CHARS = 4000

_TERM_SEPARATORS = re.compile(r"[,;\n\r]+")


class TermsError(ValueError):
    """User-facing validation error for a vocabulary or glossary."""


def dedupe_terms(terms: list[str]) -> list[str]:
    """Drop duplicates ignoring case, keeping the first spelling and the order."""
    seen, result = set(), []
    for term in terms:
        key = term.casefold()
        if key not in seen:
            seen.add(key)
            result.append(term)
    return result


def parse_terms(raw: str | list[str] | None, *, max_terms: int, label: str) -> list[str]:
    if raw is None:
        return []
    pieces = raw if isinstance(raw, list) else [raw]
    terms = [term.strip() for piece in pieces for term in _TERM_SEPARATORS.split(piece or "")]
    terms = dedupe_terms([term for term in terms if term])
    for term in terms:
        if len(term) > TERM_MAX_CHARS:
            raise TermsError(f"{label} : le terme « {term[:20]}… » dépasse {TERM_MAX_CHARS} caractères")
    if len(terms) > max_terms:
        raise TermsError(f"{label} : {len(terms)} termes, limite {max_terms}")
    return terms


def parse_vocabulary(raw: str | None) -> list[str]:
    if raw and len(raw) > VOCABULARY_MAX_CHARS:
        raise TermsError(f"Vocabulaire : {len(raw)} caractères, limite {VOCABULARY_MAX_CHARS}")
    return parse_terms(raw, max_terms=VOCABULARY_MAX_TERMS, label="Vocabulaire")


def parse_glossary(raw: list[str]) -> list[str]:
    return parse_terms(raw, max_terms=GLOSSARY_MAX_TERMS, label="Glossaire")


def join_terms(terms: list[str]) -> str | None:
    return "\n".join(terms) if terms else None


def split_stored_terms(stored: str | None) -> list[str]:
    return [term for term in (stored or "").split("\n") if term]


def effective_vocabulary(video_terms: list[str], glossary_terms: list[str]) -> list[str]:
    """Video terms first: they win over the glossary spelling (F-11.12)."""
    return dedupe_terms([*video_terms, *glossary_terms])


def _terms_within(terms: list[str], max_chars: int) -> list[str]:
    kept, used = [], 0
    for term in terms:
        cost = len(term) + (2 if kept else 0)  # ", " separator
        if used + cost > max_chars:
            break
        kept.append(term)
        used += cost
    return kept


def whisper_terms(terms: list[str]) -> list[str]:
    """Whisper's initial prompt is short (~220 useful tokens): keep the first terms."""
    return _terms_within(terms, WHISPER_VOCABULARY_MAX_CHARS)


def whisper_initial_prompt(terms: list[str]) -> str | None:
    kept = whisper_terms(terms)
    return f"Termes : {', '.join(kept)}." if kept else None


def whisper_hotwords(terms: list[str]) -> str | None:
    """The same terms for every 30 s window (the prompt only reaches the first; see transcribe_windows)."""
    kept = whisper_terms(terms)
    return " ".join(kept) if kept else None


def llm_terms(terms: list[str]) -> list[str]:
    return _terms_within(terms, LLM_VOCABULARY_MAX_CHARS)


# --- Chat language fallback (F-7.2) -------------------------------------------

_WORD = re.compile(r"[^\W\d_]{2,}")


def is_ambiguous_question(question: str) -> bool:
    """Too short to tell its language: fewer than three words of letters."""
    return len(_WORD.findall(question)) < 3


def chat_fallback_language(
    previous_user_questions: list[str],
    target_language: str | None,
    detected_language: str | None,
) -> str:
    """Language for an ambiguous question: last clear question, then the video's languages."""
    for question in reversed(previous_user_questions):
        if not is_ambiguous_question(question):
            return f"la langue de cette question précédente : « {question[:200]} »"
    return language_name(target_language) or language_name(detected_language) or "français"
