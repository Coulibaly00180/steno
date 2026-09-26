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
import re
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

# Bumped when the transcription itself changes: the quality runs (n°4)
# transcribe the reference corpus again instead of reusing their cache.
TRANSCRIPTION_VERSION = 2

# What Whisper invents over silence or music, learned from subtitled videos
# (feuille de route n° 3, phase 2; measured on the corpus: six « Merci. » of
# exactly one second over the closing music of clip-fr). Credits are never
# said in a meeting: always dropped. Stock phrases can be (« merci d'avoir
# regardé » closes a real talk): dropped only when Whisper itself doubted
# there was any speech.
_CREDITS = re.compile(
    r"sous-?titr\w*\s+(?:réalisés?\s+|faits?\s+)?par|amara\.org|sous-?titrage\s+st'?\s*\d+|subtitles?\s+by|"
    r"untertitel\s+(?:im\s+auftrag|von)|titulky\s+vytvořil|ondertiteld\s+door",
    re.IGNORECASE,
)
_STOCK = re.compile(
    r"^(?:merci(?:\s+beaucoup)?|merci\s+d'avoir\s+regardé(?:\s+cette\s+vidéo)?|abonnez-vous(?:\s+à\s+la\s+chaîne)?|"
    r"n'oubliez\s+pas\s+de\s+vous\s+abonner|thank\s+you(?:\s+very\s+much)?|thanks(?:\s+for\s+watching)?|"
    r"thank\s+you\s+for\s+watching|please\s+subscribe|bye(?:\s+bye)?)[\s.!?…]*$",
    re.IGNORECASE,
)
# Whisper's own estimate that a segment holds no speech, above which a stock phrase is dropped.
NO_SPEECH_STOCK = 0.5
# A word group repeated this many times in a row is a loop, not a way of speaking
# (« non, non, non » stays: three times).
LOOP_REPEATS = 4
LOOP_MAX_WORDS = 6
# Identical lines kept in a row: two « Merci. » can be two people; six are a loop.
MAX_IDENTICAL_LINES = 2

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
    hotwords: str | None = None,
) -> tuple[list[tuple[float, float, str]], str | None]:
    """Transcribe window by window; return (start, end, text) rows and the language.

    The language found in the first window is imposed on the next ones, so that a
    passage in another language does not switch the whole transcript.

    `doubts`: when given, word timings are asked for, and it receives one list per
    row of its doubtful words, as [start, end, probability %] character ranges.

    Each segment is decoded without the previous one's text as context: with
    it, Whisper locked onto a line and repeated it (corpus: 5 repeated lines on
    clip-fr, 2 on reunion-fr; none without). The vocabulary then goes in as
    `hotwords` too, which reach every 30 s window, where `initial_prompt` only
    reaches the first one: measured on 48 occurrences of three rare terms,
    16/16, 16/16 and 16/16 with both, against 6, 2 and 1 with the prompt alone.
    """
    rows: list[tuple[float, float, str]] = []
    detected = language
    for offset, end, audio in iter_audio_windows(audio_path, window_seconds):
        segments, info = model.transcribe(
            audio, beam_size=beam_size, vad_filter=True, language=detected, initial_prompt=initial_prompt,
            condition_on_previous_text=False, hotwords=hotwords,
            **({"word_timestamps": True} if doubts is not None else {}),
        )
        if detected is None:
            detected = getattr(info, "language", None)
        for segment in segments:
            if heartbeat:
                heartbeat()
            text = collapse_loops(segment.text.strip())
            if text and not is_invented(text, getattr(segment, "no_speech_prob", 0.0) or 0.0) \
                    and not repeats_previous(rows, text):
                rows.append((offset + float(segment.start), offset + float(segment.end), text))
                if doubts is not None:
                    doubts.append(doubtful_words(segment, text))
                if on_progress:
                    on_progress(offset + float(segment.end))
        if on_progress:
            on_progress(end)
    return rows, detected


def is_invented(text: str, no_speech_prob: float) -> bool:
    """A line Whisper made up: subtitle credits, or a stock phrase where it heard no speech."""
    if _CREDITS.search(text):
        return True
    return no_speech_prob > NO_SPEECH_STOCK and bool(_STOCK.match(text))


def collapse_loops(text: str) -> str:
    """A word group repeated LOOP_REPEATS times or more in a row, kept once."""
    words = text.split()
    if len(words) < LOOP_REPEATS:
        return text
    fold = [word.casefold().strip(".,;:!?…") for word in words]
    kept: list[str] = []
    index = 0
    while index < len(words):
        collapsed = False
        for size in range(1, LOOP_MAX_WORDS + 1):
            unit = fold[index:index + size]
            if len(unit) < size:
                break
            count = 1
            while fold[index + count * size:index + (count + 1) * size] == unit:
                count += 1
            if count >= LOOP_REPEATS:
                # The last repetition is kept: its punctuation ends the phrase.
                kept.extend(words[index + (count - 1) * size:index + count * size])
                index += count * size
                collapsed = True
                break
        if not collapsed:
            kept.append(words[index])
            index += 1
    return " ".join(kept)


def repeats_previous(rows: list[tuple[float, float, str]], text: str) -> bool:
    """True when the MAX_IDENTICAL_LINES rows before already say exactly this."""
    if len(rows) < MAX_IDENTICAL_LINES:
        return False
    key = text.casefold()
    return all(row[2].casefold() == key for row in rows[-MAX_IDENTICAL_LINES:])


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
