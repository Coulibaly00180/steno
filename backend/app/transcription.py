"""Windowed transcription: memory stays constant whatever the media duration.

faster-whisper decodes a whole file into a float32 array before its VAD step:
a 3-hour recording peaked at 2.5 GB of RAM before inference (5 GB for the
6 hours the product accepts), and the worker was killed (SIGKILL, out of
memory) next to Ollama. The 16 kHz mono WAV extracted by the pipeline is read
window by window instead; each window ends at the quietest moment of its last
seconds, so that no word is cut in half.
"""
import logging
import os
import threading
import time
import wave
from collections.abc import Callable, Iterator
from pathlib import Path

import numpy as np

WINDOW_SECONDS = 600
CUT_SEARCH_SECONDS = 15
CUT_FRAME_SECONDS = 0.2
# A word Whisper gives below this probability is shown as doubtful (n°1).
# Measured on an 11-minute review (large-v3-turbo, 2026-09-24): 2.2 % of the
# words below 0.5, among them real errors (« discounters » for Cdiscount);
# 9 % below 0.8, too many to review. Short function words are never shown:
# « Alors », « et », « on » came low there, and are never worth checking.
DOUBT_PROBABILITY = 0.5
_FUNCTION_WORDS = frozenset(
    "alors mais donc et ou où on si va le la les un une des de du au aux en ce ça c ne pas je tu il elle nous vous ils elles "
    "y a à est sont que qui quoi oui non bon ben euh hein voilà the a an and or of to is it so yes no".split()
)

logger = logging.getLogger(__name__)


class StallWatchdog:
    """Kill the process when no progress is reported for `timeout` seconds.

    Observed on the reference corpus (2026-09-23, RTX 5080): a 3-hour
    transcription froze inside CTranslate2 and the job sat at 39 % for six
    hours until RQ's timeout. The same file transcribed in 3.5 minutes when
    replayed (5 to 15 s per 10-minute window). A native call that never returns
    cannot be interrupted from Python, so the watchdog records the failure
    through `on_stall`, then ends the RQ work-horse process.
    """

    def __init__(self, timeout: float, on_stall: Callable[[], None],
                 exit_process: Callable[[int], None] = os._exit, poll: float = 5.0):
        self.timeout = timeout
        self.on_stall = on_stall
        self.exit_process = exit_process
        self.poll = poll
        self._last = time.monotonic()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._watch, name="transcription-watchdog", daemon=True)

    def beat(self) -> None:
        self._last = time.monotonic()

    def _watch(self) -> None:
        while not self._stop.wait(self.poll):
            if time.monotonic() - self._last > self.timeout:
                logger.error("Transcription stalled for more than %d s; ending the job process", self.timeout)
                try:
                    self.on_stall()
                finally:
                    self.exit_process(1)
                return

    def __enter__(self) -> "StallWatchdog":
        self.beat()
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join()


def quietest_cut(samples: np.ndarray, rate: int, search_seconds: float = CUT_SEARCH_SECONDS,
                 frame_seconds: float = CUT_FRAME_SECONDS) -> int:
    """Sample index at the centre of the lowest-energy frame of the last `search_seconds`."""
    frame = max(1, int(frame_seconds * rate))
    start = max(0, len(samples) - int(search_seconds * rate))
    usable = (len(samples) - start) // frame * frame
    if usable == 0:
        return len(samples)
    region = samples[start:start + usable].astype(np.float32).reshape(-1, frame)
    quietest = int(np.argmin((region ** 2).mean(axis=1)))
    return start + quietest * frame + frame // 2


def iter_audio_windows(path: Path, window_seconds: float = WINDOW_SECONDS, search_seconds: float = CUT_SEARCH_SECONDS,
                       frame_seconds: float = CUT_FRAME_SECONDS) -> Iterator[tuple[float, float, np.ndarray]]:
    """Yield (start, end in seconds, float32 samples) for a 16-bit mono WAV."""
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError("Expected a 16-bit mono WAV")
        rate = wav.getframerate()
        window = int(window_seconds * rate)
        carry = np.zeros(0, dtype=np.int16)
        consumed = 0
        while True:
            wanted = window - len(carry)
            data = np.frombuffer(wav.readframes(wanted), dtype=np.int16)
            samples = np.concatenate([carry, data])
            if len(samples) == 0:
                return
            last = len(data) < wanted
            cut = len(samples) if last else quietest_cut(samples, rate, search_seconds, frame_seconds)
            yield consumed / rate, (consumed + cut) / rate, samples[:cut].astype(np.float32) / 32768.0
            carry = samples[cut:]
            consumed += cut
            if last:
                return


def transcribe_windows(
    model,
    audio_path: Path,
    *,
    language: str | None,
    initial_prompt: str | None,
    beam_size: int,
    on_progress: Callable[[float], None] | None = None,
    window_seconds: float = WINDOW_SECONDS,
    heartbeat: Callable[[], None] | None = None,
    doubts: list | None = None,
) -> tuple[list[tuple[float, float, str]], str | None]:
    """Transcribe window by window; return (start, end, text) rows and the language.

    The language found in the first window is imposed on the next ones, so that a
    passage in another language does not switch the whole transcript.

    `doubts`: when given, word timings are asked for, and it receives one list per
    row of its doubtful words, as [start, end, probability %] character ranges.
    """
    rows: list[tuple[float, float, str]] = []
    detected = language
    for offset, end, audio in iter_audio_windows(audio_path, window_seconds):
        segments, info = model.transcribe(
            audio, beam_size=beam_size, vad_filter=True, language=detected, initial_prompt=initial_prompt,
            **({"word_timestamps": True} if doubts is not None else {}),
        )
        if detected is None:
            detected = getattr(info, "language", None)
        for segment in segments:
            if heartbeat:
                heartbeat()
            text = segment.text.strip()
            if text:
                rows.append((offset + float(segment.start), offset + float(segment.end), text))
                if doubts is not None:
                    doubts.append(doubtful_words(segment, text))
                if on_progress:
                    on_progress(offset + float(segment.end))
        if on_progress:
            on_progress(end)
    return rows, detected


def doubtful_words(segment, text: str, threshold: float = DOUBT_PROBABILITY) -> list[list[int]]:
    """[start, end, probability %] of the words of `text` Whisper was unsure of."""
    found = []
    cursor = 0
    for word in getattr(segment, "words", None) or []:
        token = (word.word or "").strip()
        if not token:
            continue
        start = text.find(token, cursor)
        if start < 0:
            continue
        cursor = start + len(token)
        probability = float(getattr(word, "probability", 1.0) or 0.0)
        bare = token.strip(".,;:!?…«»\"'()-").casefold()
        if len(bare) < 2 or bare in _FUNCTION_WORDS:
            continue
        if probability < threshold:
            found.append([start, cursor, round(probability * 100)])
    return found
