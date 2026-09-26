"""Nemotron 3 Diarization (feuille de route n° 3, phase 3): features, speaker cache, turns, engine choice.

The network itself is not in the test image; its output was checked against
Hugging Face transformers with bench/nemotron_reference.py (docs/specs/nemotron.md).
"""
import subprocess
import wave
from pathlib import Path

import numpy as np
import pytest

from app import diarization, nemotron
from app.diarization import Turn

HERE = Path(__file__).resolve().parent


def write_wav(path: Path, samples: np.ndarray) -> Path:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())
    return path


def test_log_mel_matches_transformers(tmp_path):
    # Reference: the first 150 frames of transformers' NemotronAsrStreamingFeatureExtractor on this file.
    reference = np.load(HERE / "nemotron_reference_mel.npy")
    wav = tmp_path / "dialogue.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(HERE.parent / "bench" / "cases" / "dialogue" / "audio.ogg"),
                    "-t", "5", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], check=True)
    audio = nemotron.Audio(wav)
    try:
        assert np.abs(audio.log_mel(0, 150) - reference).max() < 1e-3
        # A stretch read on its own (the pre-emphasis takes the sample before it) gives the same frames.
        assert np.abs(audio.log_mel(40, 100) - reference[40:140]).max() < 1e-3
    finally:
        audio.close()


def test_frames_past_the_end_are_zero(tmp_path):
    audio = nemotron.Audio(write_wav(tmp_path / "a.wav", np.random.default_rng(0).uniform(-0.5, 0.5, 1000)))
    try:
        assert (audio.frames, audio.valid) == (7, 6)
        features = audio.log_mel(0, 16)
        assert features.shape == (16, nemotron.MELS)
        assert np.all(features[:6] != 0) and np.all(features[6:] == 0)
    finally:
        audio.close()


def test_other_formats_are_refused(tmp_path):
    path = tmp_path / "stereo.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0" * 400)
    with pytest.raises(ValueError):
        nemotron.Audio(path)


def two_speakers(frames: int) -> tuple[np.ndarray, np.ndarray]:
    """Frame i's embedding holds i; speaker 1 talks in the first half, speaker 2 in the second."""
    embeds = np.repeat(np.arange(frames, dtype=np.float32)[:, None], nemotron.HIDDEN, axis=1)
    probs = np.zeros((frames, nemotron.SPEAKERS), dtype=np.float32)
    probs[: frames // 2, 0] = 0.9
    probs[frames // 2:, 1] = 0.9
    return embeds, probs


def test_compress_keeps_each_speakers_frames_in_order_with_silence_slots():
    embeds, probs = two_speakers(400)
    silence = np.full(nemotron.HIDDEN, -1.0, dtype=np.float32)
    kept, kept_probs = nemotron.compress(embeds, probs, silence)
    assert kept.shape == (nemotron.CACHE, nemotron.HIDDEN) and kept_probs.shape == (nemotron.CACHE, nemotron.SPEAKERS)
    frames = kept[:, 0]
    silent = frames == -1.0
    # One learned-silence slot per speaker, and the silent speakers' share goes to silence too.
    assert silent.sum() >= nemotron.SPEAKERS and np.all(kept_probs[silent] == 0)
    first, second = frames[~silent & (frames < 200)], frames[~silent & (frames >= 200)]
    # Both speakers keep at least their boosted frames (2 x 24).
    assert len(first) >= 48 and len(second) >= 48 and len(first) + len(second) == nemotron.CACHE - silent.sum()
    # Grouped by speaker, in time order within a speaker.
    assert np.all(np.diff(first) > 0) and np.all(np.diff(second) > 0)
    assert np.flatnonzero(frames == first[-1])[0] < np.flatnonzero(frames == second[0])[0]


def test_the_fifo_overflows_into_the_cache_which_is_then_compressed():
    cache = nemotron.SpeakerCache()
    chunk = nemotron.CHUNK
    embeds, _ = two_speakers(chunk + nemotron.RIGHT_CONTEXT)
    logits = np.full(((chunk + nemotron.RIGHT_CONTEXT) * nemotron.FACTOR, nemotron.SPEAKERS), -5.0, dtype=np.float32)
    logits[: chunk * 4, 0] = 5.0
    logits[chunk * 4:, 1] = 5.0
    silence = np.zeros(nemotron.HIDDEN, dtype=np.float32)
    cache.update(embeds, logits, silence, chunk, np.ones(len(embeds), dtype=np.float32))
    # 340 frames in a 40-frame FIFO: 300 move to the cache, over its 264 slots, so it is compressed.
    assert cache.compressed and len(cache.embeds) == nemotron.CACHE
    assert np.array_equal(cache.fifo[:, 0], np.arange(chunk - nemotron.FIFO, chunk, dtype=np.float32))
    assert len(cache.cached()) == nemotron.CACHE + nemotron.FIFO
    # A short chunk only fills the FIFO.
    small = nemotron.SpeakerCache()
    small.update(embeds[:10], logits[:80], silence, 10, np.ones(10, dtype=np.float32))
    assert len(small.embeds) == 0 and len(small.fifo) == 10 and not small.compressed


def activity(seconds: float, *spans: tuple[int, float, float]) -> np.ndarray:
    active = np.zeros((int(seconds * 100), nemotron.SPEAKERS), dtype=bool)
    for speaker, start, end in spans:
        active[int(start * 100):int(end * 100), speaker] = True
    return active


def test_turns_bridge_pauses_and_drop_blips():
    turns = nemotron.turns_from_activity(activity(
        40, (0, 0, 10), (0, 11, 20),  # a 1 s breath inside speaker 0's speech
        (3, 20.5, 30), (3, 32, 38),  # a 2 s pause: two turns
        (5, 25, 25.2),  # a 0.2 s blip: dropped... and its speaker with it
    ))
    assert sorted(turns) == [(0.0, 20.0, 0), (20.5, 30.0, 3), (32.0, 38.0, 3)]


def test_a_voice_heard_for_a_moment_joins_the_nearest_speaker():
    turns = nemotron.turns_from_activity(activity(60, (0, 0, 25), (1, 26, 50), (2, 50.5, 52)))
    assert sorted(turns) == [(0.0, 25.0, 0), (26.0, 50.0, 1), (50.5, 52.0, 1)]
    # With the number of speakers given, the largest ones stay.
    assert {s for *_, s in nemotron.turns_from_activity(activity(60, (0, 0, 25), (1, 26, 50), (2, 50.5, 52)), 3)} == {0, 1, 2}
    assert {s for *_, s in nemotron.turns_from_activity(activity(60, (0, 0, 5), (1, 6, 50), (2, 51, 58)), 2)} == {1, 2}


def test_the_engine_falls_back_to_sherpa(monkeypatch):
    monkeypatch.setattr(diarization.settings, "diarization_engine", "nemotron")
    monkeypatch.setattr(nemotron, "model_available", lambda: True)
    assert diarization.engine_for() == "nemotron"
    assert diarization.engine_for(8) == "nemotron"
    assert diarization.engine_for(9) == "sherpa"  # Nemotron follows up to eight voices
    assert diarization.engine_for(engine="sherpa") == "sherpa"
    monkeypatch.setattr(nemotron, "model_available", lambda: False)
    assert diarization.engine_for() == "sherpa"


def test_model_files_are_both_needed(monkeypatch, tmp_path):
    monkeypatch.setattr(nemotron.settings, "diarization_models_dir", tmp_path)
    (tmp_path / "nemotron").mkdir()
    (tmp_path / "nemotron" / nemotron.MODEL_FILE).write_bytes(b"graph")
    assert not nemotron.model_available()
    (tmp_path / "nemotron" / (nemotron.MODEL_FILE + "_data")).write_bytes(b"weights")
    assert nemotron.model_available()


def test_diarize_numbers_nemotron_speakers_by_appearance(monkeypatch, tmp_path):
    monkeypatch.setattr(diarization.settings, "diarization_engine", "nemotron")
    monkeypatch.setattr(nemotron, "model_available", lambda: True)
    monkeypatch.setattr(nemotron, "activity", lambda path, on_progress=None: activity(30, (4, 0, 10), (2, 12, 25)))
    assert diarization.diarize(tmp_path / "a.wav") == [Turn(0.0, 10.0, 1), Turn(12.0, 25.0, 2)]
