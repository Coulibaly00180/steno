"""The voices bench's scores (bench/run.py): the matching of labels, the words, the thresholds."""
import json

from app.diarization import Turn
from bench import run

TRUTH = {
    "language": "fr",
    "speakers": ["Claire", "Pierre"],
    "duration": 10.0,
    "lines": [
        {"speaker": "Claire", "start": 0.0, "end": 4.0, "text": "Bonjour Pierre, on commence."},
        {"speaker": "Pierre", "start": 5.0, "end": 9.0, "text": "Oui, le budget est prêt."},
    ],
}


def test_labels_are_matched_one_to_one_whatever_their_numbers():
    # The diarization calls Pierre 1 and Claire 2: the best matching still gives no error.
    scores = run.speaker_scores(TRUTH, [Turn(0.0, 4.0, 2), Turn(5.0, 9.0, 1)])
    assert scores["speaker_error"] == 0.0 and scores["voices"] == "2/2" and scores["found_all"]


def test_a_merged_voice_and_a_missed_stretch_count_as_errors():
    # Both people given to one speaker: half the speech is on the wrong person.
    merged = run.speaker_scores(TRUTH, [Turn(0.0, 9.0, 1)])
    assert abs(merged["speaker_error"] - 0.5) < 0.01 and merged["voices"] == "1/2" and not merged["found_all"]
    # Pierre's turn not found at all: missed, also an error.
    missed = run.speaker_scores(TRUTH, [Turn(0.0, 4.0, 1)])
    assert abs(missed["speaker_error"] - 0.5) < 0.01 and abs(missed["missed"] - 0.5) < 0.01


def test_overlapped_speech_is_left_out():
    truth = TRUTH | {"lines": TRUTH["lines"] + [{"speaker": "Pierre", "start": 2.0, "end": 4.0, "text": "D'accord."}]}
    # 2 to 4 s: both talk; only the frames of one person at a time are scored.
    scores = run.speaker_scores(truth, [Turn(0.0, 4.0, 1), Turn(5.0, 9.0, 2)])
    assert scores["speaker_error"] == 0.0


def test_words_and_person():
    rows = [(0.0, 4.0, "Bonjour Pierre, on commence."), (5.0, 9.0, "Oui, le budget est pret.")]
    result = run.text_scores(TRUTH, rows, [1, 2], {1: 0, 2: 1})
    # Accents are ignored: « prêt » heard as « pret » counts.
    assert result["words_found"] == 1.0 and result["person"] == 1.0 and result["lines"] == 2
    swapped = run.text_scores(TRUTH, rows, [2, 1], {1: 0, 2: 1})
    assert swapped["person"] == 0.0


def test_a_regression_fails_the_check(tmp_path, monkeypatch):
    (tmp_path / "thresholds.json").write_text(json.dumps({
        "dialogue": {"max": {"speaker_error": 0.03}, "min": {"words_found": 0.95}, "all_voices": True},
        "silence": {"max": {"lines": 0}},
    }))
    monkeypatch.setattr(run, "HERE", tmp_path)
    assert run.check([{"case": "dialogue", "speaker_error": 0.01, "words_found": 0.97, "found_all": True}]) == []
    failures = run.check([
        {"case": "dialogue", "speaker_error": 0.2, "words_found": 0.9, "found_all": False, "voices": "1/2"},
        {"case": "silence", "lines": 3},
    ])
    assert len(failures) == 4 and any("silence" in failure for failure in failures)
