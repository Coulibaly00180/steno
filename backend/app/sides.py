"""The two sides of a recorded call (feuille de route n° 3, phase 4).

In « micro + onglet » mode the browser records your microphone on the left
channel and the tab's (or the system's) sound on the right one, instead of
mixing them. Each side is then transcribed on its own: who said a line is
read off its track rather than guessed, and two people talking at once are
both kept, where Whisper, following one voice at a time, dropped the quieter.

Without a headset, the other side comes back into your microphone, a little
later and quieter than on its own track. Two filters remove it, after
omarchy-meeting-recorder (MIT):
- by level: a microphone frame only counts as your voice when it is at least
  half as loud as the loudest sound of the other side around it, both tracks
  first brought to a similar speaking level; the rest of the microphone is
  silenced before Whisper hears it;
- by text: a line of yours that repeats, at the same moment, half or more of
  the word triples of the other side is their voice, and goes.

Memory stays flat up to 6 hours: the tracks are read block by block, and only
one level per 30 ms frame is kept (3 MB per track for 6 hours).
"""
import bisect
import contextlib
import re
import subprocess
import tempfile
import unicodedata
import wave
from collections.abc import Callable, Generator, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .config import settings
from .transcription import transcribe_windows

YOU, OTHERS = "you", "others"
RATE = 16000
FRAME = RATE * 30 // 1000  # 30 ms
BLOCK_FRAMES = 2000  # read 60 s at a time
# A frame has sound when it is over 4 times the track's noise floor (its 10th percentile).
FLOOR_PERCENTILE = 10
FLOOR_FACTOR = 4.0
MIN_LEVEL = 0.002
# Speaking level both tracks are brought to before they are compared.
TARGET_LEVEL = 0.1
GAIN_RANGE = (0.25, 8.0)
# Your voice: at least half the loudest level of the other side within ±3 frames
# (the echo trails behind), in runs of 3 frames or more.
ECHO_RATIO = 0.5
AROUND = 3
RUN = 3
# Kept around your voice so word edges survive; closer stretches are joined.
PAD_SECONDS = 0.3
MERGE_SECONDS = 0.8
# A line of yours is compared with the other side's lines within 2 s of it.
ECHO_WINDOW_SECONDS = 2.0
# A line is cut where two of its words are further apart (see transcribe_windows).
SPLIT_PAUSE_SECONDS = 1.5


@dataclass
class Prepared:
    you: Path  # your microphone, the other side's echo silenced
    others: Path
    you_seconds: float  # how much speech each side holds, to pick the one to detect the language on
    others_seconds: float
    duration: float  # seconds of the recording


def is_two_sided(path: Path) -> bool:
    """A stereo file: the left channel is the microphone, the right one the other side."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=channels", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=settings.ffprobe_timeout_seconds,
    )
    try:
        return int(result.stdout.strip().split(",")[0]) >= 2
    except ValueError:
        return False


def split_channels(source: Path, left: Path, right: Path) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-loglevel", "error", "-i", str(source), "-vn",
         "-filter_complex", "[0:a:0]channelsplit=channel_layout=stereo[l][r]",
         "-map", "[l]", "-ar", str(RATE), "-c:a", "pcm_s16le", str(left),
         "-map", "[r]", "-ar", str(RATE), "-c:a", "pcm_s16le", str(right)],
        check=True, capture_output=True, timeout=settings.ffmpeg_timeout_seconds,
    )


def _blocks(path: Path) -> Iterator[np.ndarray]:
    with wave.open(str(path), "rb") as wav:
        while True:
            data = wav.readframes(FRAME * BLOCK_FRAMES)
            if not data:
                return
            yield np.frombuffer(data, dtype=np.int16).astype(np.float32) / 32768.0


def frame_levels(path: Path) -> np.ndarray:
    """RMS of every 30 ms frame of a 16 kHz mono WAV."""
    levels = []
    for block in _blocks(path):
        padded = np.pad(block, (0, -len(block) % FRAME))
        levels.append(np.sqrt(np.mean(padded.reshape(-1, FRAME) ** 2, axis=1)))
    return np.concatenate(levels) if levels else np.zeros(0, dtype=np.float32)


def active_frames(levels: np.ndarray) -> np.ndarray:
    if not len(levels):
        return np.zeros(0, dtype=bool)
    floor = float(np.percentile(levels, FLOOR_PERCENTILE))
    return levels >= max(floor * FLOOR_FACTOR, MIN_LEVEL)


def speaking_gain(levels: np.ndarray) -> float:
    """What brings the track's speech to TARGET_LEVEL; a track of noise only is left as it is."""
    active = levels[active_frames(levels)]
    level = float(np.mean(active)) if len(active) else 0.0
    return 1.0 if level < 0.003 else float(np.clip(TARGET_LEVEL / level, *GAIN_RANGE))


def own_voice(mic: np.ndarray, other: np.ndarray) -> np.ndarray:
    """Frames of the microphone that are your voice, not the other side coming back through the speakers."""
    frames = max(len(mic), len(other))
    mic = np.pad(mic, (0, frames - len(mic)))
    other = np.pad(other, (0, frames - len(other)))
    active = active_frames(mic)
    if frames:
        windows = np.lib.stride_tricks.sliding_window_view(np.pad(other, AROUND), 2 * AROUND + 1)
        loudest = windows.max(axis=1)
        # The microphone's speaking level is taken while the other side is quiet: your voice
        # alone. Taken over all its sound, the echo of someone who barely speaks became their
        # "speech", was brought up to speaking level, and passed.
        quiet = ~np.convolve(active_frames(other), np.ones(2 * AROUND + 1), mode="same").astype(bool)
        mine = mic[quiet]
        mic_gain = speaking_gain(mine) if len(mine) else 1.0
        active &= mic * mic_gain * (1 / ECHO_RATIO) >= loudest * speaking_gain(other)
    # Runs shorter than RUN frames go: the gaps between their words must not let the echo through.
    edges = np.diff(np.concatenate([[0], active.astype(np.int8), [0]]))
    for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
        if end - start < RUN:
            active[start:end] = False
    return active


def regions(active: np.ndarray) -> list[tuple[int, int]]:
    """Sample ranges around the active frames, padded, close ones joined."""
    pad, merge = int(PAD_SECONDS * RATE), int(MERGE_SECONDS * RATE)
    result: list[list[int]] = []
    edges = np.diff(np.concatenate([[0], active.astype(np.int8), [0]]))
    for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
        a, b = max(0, start * FRAME - pad), end * FRAME + pad
        if result and a <= result[-1][1] + merge:
            result[-1][1] = max(result[-1][1], b)
        else:
            result.append([a, b])
    return [(a, b) for a, b in result]


def keep_only(source: Path, target: Path, kept: list[tuple[int, int]]) -> None:
    """`source` with everything outside `kept` silenced, written block by block."""
    with wave.open(str(target), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        offset, index = 0, 0
        for block in _blocks(source):
            mask = np.zeros(len(block), dtype=bool)
            while index < len(kept) and kept[index][1] <= offset:
                index += 1
            for a, b in kept[index:]:
                if a >= offset + len(block):
                    break
                mask[max(a - offset, 0):b - offset] = True
            out.writeframes((np.where(mask, block, 0.0) * 32767).astype(np.int16).tobytes())
            offset += len(block)


@contextlib.contextmanager
def prepared(source: Path) -> Generator[Prepared, None, None]:
    """Both sides as 16 kHz mono WAVs (your side cleaned of echo), in a folder removed afterwards."""
    settings.audio_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=settings.audio_dir, prefix=".sides-") as folder:
        mic, others, you = Path(folder) / "mic.wav", Path(folder) / "others.wav", Path(folder) / "you.wav"
        split_channels(source, mic, others)
        mic_levels, other_levels = frame_levels(mic), frame_levels(others)
        mine = own_voice(mic_levels, other_levels)
        keep_only(mic, you, regions(mine))
        mic.unlink()
        seconds = FRAME / RATE
        with wave.open(str(others), "rb") as wav:
            duration = wav.getnframes() / RATE
        yield Prepared(you, others, float(mine.sum() * seconds), float(active_frames(other_levels).sum() * seconds), duration)


def half_progress(on_progress: Callable[[float], None], index: int, duration: float) -> Callable[[float], None]:
    """Progress of pass `index` (0 or 1) as seconds of the whole two-pass work, scaled to the recording."""
    return lambda done: on_progress((index * duration + done) / 2)


def transcribe(model, source: Path, *, language: str | None, on_progress: Callable[[float], None] | None = None,
               doubts: list | None = None, **options) -> tuple[list[tuple[float, float, str, str]], str | None]:
    """Each side through Whisper on its own, then merged: (start, end, text, side) rows and the language.

    The side with the most speech goes first: with no language given, the one
    it is in counts for both. `on_progress` gets seconds of the recording, the
    two passes each taking half of it. `doubts`, when given, receives one list
    per row, as with `transcribe_windows`.
    """
    with prepared(source) as sides:
        duration = sides.duration
        passes = sorted([(YOU, sides.you, sides.you_seconds), (OTHERS, sides.others, sides.others_seconds)],
                        key=lambda item: -item[2])
        heard: dict[str, tuple[list, list]] = {YOU: ([], []), OTHERS: ([], [])}
        detected = language
        for index, (side, path, seconds) in enumerate(passes):
            if seconds <= 0:
                continue
            progress = half_progress(on_progress, index, duration) if on_progress else None
            # Word timings always: a side is mostly silence, and without them Whisper stretched a
            # line over the silence up to the next one (bench « appel »: 1.0-3.2 s said, 0.6-11.3 s given).
            side_doubts: list = []
            rows, found = transcribe_windows(model, path, language=detected, on_progress=progress,
                                             doubts=side_doubts, split_pauses=SPLIT_PAUSE_SECONDS, **options)
            detected = detected or found
            heard[side] = (rows, side_doubts)
    merged = merge(heard[YOU][0], heard[OTHERS][0], heard[YOU][1], heard[OTHERS][1])
    if doubts is not None:
        doubts.extend(row[4] for row in merged)
    return [row[:4] for row in merged], detected


def _words(text: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", text.casefold()).encode("ascii", "ignore").decode()
    return re.findall(r"[a-z0-9]+", folded)


def _triples(words: list[str]) -> list[tuple[str, ...]]:
    return [tuple(words[i:i + 3]) for i in range(len(words) - 2)]


class OtherSide:
    """The other side's lines, with their words, found by time."""

    def __init__(self, lines: list[tuple[float, float, str]]):
        self.lines = sorted(lines, key=lambda row: row[0])
        self.starts = [row[0] for row in self.lines]
        self.words = [_words(row[2]) for row in self.lines]
        self.longest = max((e - s for s, e, _ in self.lines), default=0.0)

    def near(self, start: float, end: float) -> list[list[str]]:
        first = bisect.bisect_left(self.starts, start - ECHO_WINDOW_SECONDS - self.longest)
        last = bisect.bisect_left(self.starts, end + ECHO_WINDOW_SECONDS)
        return [self.words[i] for i in range(first, last) if start < self.lines[i][1] + ECHO_WINDOW_SECONDS]


def is_echo(line: tuple[float, float, str], theirs: OtherSide) -> bool:
    """A line of yours that repeats what the other side said at the same moment."""
    start, end, text = line
    near = theirs.near(start, end)
    mine = _words(text)
    if not mine or not near:
        return False
    own = _triples(mine)
    if not own:
        # One or two words: an echo when they come back word for word.
        return any(words[i:i + len(mine)] == mine for words in near for i in range(len(words) - len(mine) + 1))
    heard = {triple for words in near for triple in _triples(words)}
    return 2 * sum(triple in heard for triple in own) >= len(own)


def merge(you: list[tuple[float, float, str]], others: list[tuple[float, float, str]],
          you_doubts: list | None = None, others_doubts: list | None = None) -> list[tuple[float, float, str, str, list]]:
    """Both sides in the order they were said, the echo lines of yours dropped: (start, end, text, side, doubts)."""
    theirs = OtherSide(others)
    rows = [(*row, OTHERS, doubts) for row, doubts in zip(others, others_doubts or [[]] * len(others))]
    rows += [
        (*row, YOU, doubts) for row, doubts in zip(you, you_doubts or [[]] * len(you))
        if not is_echo(row, theirs)
    ]
    return sorted(rows, key=lambda row: (row[0], row[1]))
