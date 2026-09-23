import wave
from types import SimpleNamespace

import numpy as np
import pytest

from app import transcription

RATE = 16000


def write_wav(path, samples: np.ndarray) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(samples.astype(np.int16).tobytes())


def tone(seconds: float) -> np.ndarray:
    t = np.arange(int(seconds * RATE)) / RATE
    return (8000 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)


def silence(seconds: float) -> np.ndarray:
    return np.zeros(int(seconds * RATE), dtype=np.int16)


def test_windows_cover_the_file_and_cut_in_silence(tmp_path):
    # 25 s: speech-like tone, a silent gap at 8-9 s, then tone again.
    audio = np.concatenate([tone(8), silence(1), tone(16)])
    path = tmp_path / "a.wav"
    write_wav(path, audio)

    windows = list(transcription.iter_audio_windows(path, window_seconds=10, search_seconds=3, frame_seconds=0.2))

    assert sum(len(samples) for _, _, samples in windows) == len(audio)
    first_start, first_end, _ = windows[0]
    assert first_start == 0 and 8.0 <= first_end <= 9.0  # cut in the silence, not at 10 s
    assert all(windows[i][1] == pytest.approx(windows[i + 1][0]) for i in range(len(windows) - 1))
    assert windows[-1][1] == pytest.approx(25.0)


def test_short_file_is_a_single_window(tmp_path):
    path = tmp_path / "b.wav"
    write_wav(path, tone(3))
    assert [(start, end) for start, end, _ in transcription.iter_audio_windows(path)] == [(0.0, 3.0)]


def test_memory_is_bounded_by_the_window(tmp_path):
    path = tmp_path / "c.wav"
    write_wav(path, tone(30))
    sizes = [len(samples) for _, _, samples in transcription.iter_audio_windows(path, window_seconds=10, search_seconds=2)]
    assert max(sizes) <= 10 * RATE


def test_transcribe_windows_offsets_timestamps_and_keeps_the_language(tmp_path):
    path = tmp_path / "d.wav"
    write_wav(path, np.concatenate([tone(8), silence(1), tone(16)]))
    calls = []

    class FakeModel:
        def transcribe(self, audio, **kwargs):
            calls.append(kwargs["language"])
            length = len(audio) / RATE
            return iter([SimpleNamespace(start=0.5, end=length, text=" phrase "), SimpleNamespace(start=1, end=1, text="  ")]), \
                SimpleNamespace(language="fr")

    progress = []
    rows, language = transcription.transcribe_windows(
        FakeModel(), path, language=None, initial_prompt="Termes : A.", beam_size=5,
        on_progress=progress.append, window_seconds=10,
    )

    assert language == "fr"
    assert calls[0] is None and all(call == "fr" for call in calls[1:])  # detected once, then imposed
    assert rows[0][0] == 0.5 and rows[1][0] > 8.0  # second window shifted by its offset
    assert all(text == "phrase" for _, _, text in rows)
    assert progress[-1] == pytest.approx(25.0)


def test_rejects_non_mono_16_bit(tmp_path):
    path = tmp_path / "e.wav"
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes(b"\x00\x00\x00\x00" * 10)
    with pytest.raises(ValueError):
        list(transcription.iter_audio_windows(path))


def test_watchdog_records_the_stall_then_ends_the_process():
    import threading

    events = []
    exited = threading.Event()
    watchdog = transcription.StallWatchdog(
        0.05, on_stall=lambda: events.append("stalled"),
        exit_process=lambda code: events.append(code) or exited.set(), poll=0.01,
    )
    with watchdog:
        assert exited.wait(2)
    assert events == ["stalled", 1]


def test_watchdog_stays_quiet_while_progress_is_reported():
    import time

    events = []
    watchdog = transcription.StallWatchdog(
        0.2, on_stall=lambda: events.append("stalled"), exit_process=events.append, poll=0.01,
    )
    with watchdog:
        for _ in range(20):
            time.sleep(0.02)
            watchdog.beat()
    time.sleep(0.3)  # stopped on exit: no late kill
    assert events == []


def test_transcribe_windows_beats_on_every_segment(tmp_path):
    path = tmp_path / "audio.wav"
    write_wav(path, tone(3))

    class Model:
        def transcribe(self, audio, **kwargs):
            segments = [SimpleNamespace(start=0.0, end=1.0, text="un"), SimpleNamespace(start=1.0, end=2.0, text="deux")]
            return iter(segments), SimpleNamespace(language="fr")

    beats = []
    transcription.transcribe_windows(
        Model(), path, language=None, initial_prompt=None, beam_size=1, heartbeat=lambda: beats.append(1),
    )
    assert len(beats) == 2
