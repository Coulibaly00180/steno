import pytest

from app import analysis_options as opts


@pytest.mark.parametrize(("minutes", "length", "expected"), [
    (10, "standard", 250),
    (60, "standard", 610),
    (180, "standard", 1500),  # ceiling
    (0.5, "short", 201),
    (60, "short", 350),
    (180, "short", 450),  # ceiling
    (0.5, "detailed", 500),  # floor
    (60, "detailed", 1220),
    (360, "detailed", 2500),  # ceiling
])
def test_word_budget(minutes, length, expected):
    assert opts.word_budget(minutes * 60, length) == expected


def test_vocabulary_is_split_trimmed_and_deduplicated_ignoring_case():
    assert opts.parse_vocabulary(" Okr, OKR ;Doñana\n\nKubernetes ,okr") == ["Okr", "Doñana", "Kubernetes"]


@pytest.mark.parametrize(("raw", "message"), [
    ("x" * 1001, "1001 caractères, limite 1000"),
    ("a" * 61, "dépasse 60 caractères"),
    (",".join(f"t{i}" for i in range(101)), "101 termes, limite 100"),
])
def test_vocabulary_limits(raw, message):
    with pytest.raises(opts.TermsError, match=message):
        opts.parse_vocabulary(raw)


def test_glossary_limit_and_list_input():
    assert opts.parse_glossary(["OKR, Doñana", "  ", "okr"]) == ["OKR", "Doñana"]
    with pytest.raises(opts.TermsError, match="301 termes, limite 300"):
        opts.parse_glossary([f"terme{i}" for i in range(301)])


def test_video_terms_win_over_the_glossary():
    assert opts.effective_vocabulary(["Okr", "Alpha"], ["OKR", "Beta"]) == ["Okr", "Alpha", "Beta"]


def test_whisper_gets_the_first_terms_within_800_characters():
    terms = [f"{i:02d}" + "x" * 38 for i in range(60)]  # 40 characters each
    kept = opts.whisper_terms(terms)
    assert kept == terms[: len(kept)]
    assert len(", ".join(kept)) <= 800 < len(", ".join(terms[: len(kept) + 1]))
    assert opts.whisper_initial_prompt(["A", "B"]) == "Termes : A, B."
    assert opts.whisper_initial_prompt([]) is None
    assert len(opts.llm_terms(terms)) == 60


@pytest.mark.parametrize(("question", "ambiguous"), [
    ("ok ?", True),
    ("Paris 2024", True),
    ("What were the decisions?", False),
    ("Quelles décisions ont été prises ?", False),
])
def test_ambiguous_question(question, ambiguous):
    assert opts.is_ambiguous_question(question) is ambiguous


def test_chat_fallback_language_order():
    assert "What were the decisions?" in opts.chat_fallback_language(["What were the decisions?", "ok ?"], "espagnol", "fr")
    assert opts.chat_fallback_language(["ok ?"], "espagnol", "fr") == "espagnol"
    assert opts.chat_fallback_language([], None, "en") == "anglais"
    assert opts.chat_fallback_language([], None, None) == "français"
