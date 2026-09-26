"""A clean transcription (feuille de route n° 3, phase 2): no context, vocabulary everywhere, invented lines out."""
from types import SimpleNamespace

import numpy as np
import pytest

from app import analysis_options, transcription
from tests.test_transcription import silence, tone, write_wav


def segment(text, start, end, no_speech=0.0):
    return SimpleNamespace(start=start, end=end, text=text, no_speech_prob=no_speech, words=None)


class Model:
    def __init__(self, segments):
        self.segments, self.calls = segments, []

    def transcribe(self, audio, **options):
        self.calls.append(options)
        return iter(self.segments), SimpleNamespace(language="fr")


@pytest.fixture
def audio(tmp_path):
    path = tmp_path / "a.wav"
    write_wav(path, np.concatenate([tone(3), silence(1)]))
    return path


def test_no_context_and_the_vocabulary_as_hotwords(audio):
    model = Model([segment(" Bonjour.", 0.0, 1.0)])
    transcription.transcribe_windows(
        model, audio, language="fr", initial_prompt="Termes : Quotespec.", beam_size=5, hotwords="Quotespec",
    )
    options = model.calls[0]
    assert options["condition_on_previous_text"] is False
    assert options["initial_prompt"] == "Termes : Quotespec." and options["hotwords"] == "Quotespec"
    assert analysis_options.whisper_hotwords(["Quotespec", "Karim Benally"]) == "Quotespec Karim Benally"
    assert analysis_options.whisper_hotwords([]) is None


def test_invented_lines_are_dropped_real_ones_kept(audio):
    doubts = []
    model = Model([
        segment(" Le budget est validé.", 0.0, 3.0),
        segment(" Sous-titres réalisés par la communauté d'Amara.org", 3.0, 4.0),
        # Over the closing music: Whisper doubted there was speech.
        segment(" Merci d'avoir regardé cette vidéo !", 4.0, 5.0, no_speech=0.8),
        # Said for real at the end of a talk.
        segment(" Merci d'avoir regardé.", 5.0, 6.0, no_speech=0.1),
    ])
    rows, _ = transcription.transcribe_windows(model, audio, language="fr", initial_prompt=None, beam_size=5, doubts=doubts)
    assert [text for _, _, text in rows] == ["Le budget est validé.", "Merci d'avoir regardé."]
    # One doubt list per kept row: the rows and their doubts stay aligned.
    assert len(doubts) == len(rows)


def test_a_run_of_identical_lines_keeps_two(audio):
    model = Model([segment(" Merci.", float(second), second + 1.0) for second in range(6)] + [segment(" Au revoir.", 6.0, 7.0)])
    rows, _ = transcription.transcribe_windows(model, audio, language="fr", initial_prompt=None, beam_size=5)
    assert [text for _, _, text in rows] == ["Merci.", "Merci.", "Au revoir."]


@pytest.mark.parametrize("text, expected", [
    ("quand quand quand quand j'appelle", "quand j'appelle"),
    ("c'est vrai, c'est vrai, c'est vrai, c'est vrai, c'est vrai.", "c'est vrai."),
    ("Non, non, non, pas du tout.", "Non, non, non, pas du tout."),
    ("Le budget du trimestre.", "Le budget du trimestre."),
    ("oui", "oui"),
])
def test_loops_inside_a_line_are_kept_once(text, expected):
    assert transcription.collapse_loops(text) == expected


@pytest.mark.parametrize("text, no_speech, invented", [
    ("Sous-titrage ST' 501", 0.0, True),
    ("Subtitles by the Amara.org community", 0.0, True),
    ("Merci.", 0.9, True),
    ("Merci.", 0.2, False),
    ("Merci pour le devis, je le relis ce soir.", 0.9, False),
    ("Le sous-titrage de la vidéo est prêt.", 0.0, False),
])
def test_what_counts_as_invented(text, no_speech, invented):
    assert transcription.is_invented(text, no_speech) is invented
