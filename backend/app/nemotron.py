"""Who speaks when, with NVIDIA's Nemotron 3 Diarization (feuille de route n° 3, phase 3).

The model is a Streaming Sortformer: a transformer that reads a chunk of audio
together with a small memory of earlier frames (the speaker cache and a FIFO of
the latest ones), and gives every 10 ms frame a probability for each of up to
eight speakers, numbered in the order they are first heard. It replaces
clustering: no voice prints to group, so short turns and meetings of four or
more people no longer collapse into two voices.

The network is the int8 ONNX export of onnx-community (OpenMDW 1.1 licence,
see /opt/models/diarization/nemotron/NOTICE.md), baked into the image at a
pinned revision. It holds no state: it takes the log-mel features of one chunk
and the cached frames, and returns the logits and the chunk's frames. The
features, the chunk loop and the cache policy are here, and follow
`Nemotron3DiarizationSpeakerCache` and `NemotronAsrStreamingFeatureExtractor`
in Hugging Face transformers (offline mode), step by step.

Memory stays flat up to 6 hours: the audio is read chunk by chunk from the WAV,
and only one bit per frame and speaker (active or not) is kept.
"""
import math
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np

from .config import settings

RATE = 16000
HOP = 160
N_FFT = 512
WIN = 400
BINS = N_FFT // 2 + 1
MELS = 128
PREEMPHASIS = 0.97
LOG_GUARD = 2.0**-24
FRAME_SECONDS = HOP / RATE

# config.json of the checkpoint, offline sizes.
HIDDEN = 512
FACTOR = 8  # mel frames per encoder frame
SPEAKERS = 8
CHUNK = 340  # encoder frames per step (27.2 s)
RIGHT_CONTEXT = 40  # look-ahead encoder frames
FIFO = 40
UPDATE_PERIOD = 300
CACHE = 264
SILENCE_FRAMES = 1
SCORE_THRESHOLD = 0.25
LATEST_BOOST = 0.05
_BUDGET = CACHE // SPEAKERS - SILENCE_FRAMES
MIN_POSITIVE = math.floor(_BUDGET * 0.5)
STRONG_BOOSTED = math.floor(_BUDGET * 0.75)
WEAK_BOOSTED = math.floor(_BUDGET * 1.5)

# Turns from the probabilities.
ACTIVE = 0.5
# A pause shorter than this inside one speaker's speech is bridged. The model marks
# the voice itself, down to the breath between two phrases; the reference turns of
# the voices bench (and a reader) count those as the same turn. Measured
# (docs/specs/nemotron.md): 0.5 s left 17 % of AMI's speech to nobody, 1.5 s 6 %.
BRIDGE_SECONDS = 1.5
MIN_TURN_SECONDS = 0.3  # shorter blips are dropped
# A "speaker" with less speech than this is another one on a bad moment (a cough, a laugh, crosstalk).
MIN_SPEAKER_SECONDS = 4.0
MIN_SPEAKER_SHARE = 0.04

MODEL_FILE = "model_quantized.onnx"


def model_path() -> Path:
    return Path(settings.diarization_models_dir) / "nemotron" / MODEL_FILE


def model_available() -> bool:
    path = model_path()
    return path.is_file() and path.with_name(MODEL_FILE + "_data").is_file()


def _mel_filters() -> np.ndarray:
    """librosa.filters.mel(sr=16000, n_fft=512, n_mels=128, norm="slaney"): MELS x BINS."""
    f_sp, min_log_hz = 200.0 / 3, 1000.0
    min_log_mel, logstep = min_log_hz / f_sp, math.log(6.4) / 27.0

    def hz_to_mel(hz: float) -> float:
        return hz / f_sp if hz < min_log_hz else min_log_mel + math.log(hz / min_log_hz) / logstep

    mels = np.linspace(hz_to_mel(0.0), hz_to_mel(RATE / 2), MELS + 2)
    hz = np.where(mels < min_log_mel, f_sp * mels, min_log_hz * np.exp(logstep * (mels - min_log_mel)))
    fft_hz = np.linspace(0, RATE / 2, BINS)
    widths = np.diff(hz)
    ramps = hz[:, None] - fft_hz[None, :]
    lower = -ramps[:-2] / widths[:-1, None]
    upper = ramps[2:] / widths[1:, None]
    weights = np.maximum(0, np.minimum(lower, upper)) * (2.0 / (hz[2:] - hz[:-2]))[:, None]
    return weights.astype(np.float32)


def _window() -> np.ndarray:
    """torch.hann_window(400, periodic=False), centred in the 512-sample transform."""
    window = np.zeros(N_FFT, dtype=np.float64)
    offset = (N_FFT - WIN) // 2
    window[offset:offset + WIN] = np.hanning(WIN)
    return window


class Audio:
    """A 16-bit mono WAV read on demand: the log-mel frames of any stretch.

    Frame f is centred on sample f * HOP (torch.stft(center=True), zero
    padding), after pre-emphasis of the whole signal; frames from `valid` on
    are zero, as the feature extractor's mask makes them.
    """

    def __init__(self, path: Path):
        self._wav = wave.open(str(path), "rb")
        if self._wav.getnchannels() != 1 or self._wav.getsampwidth() != 2 or self._wav.getframerate() != RATE:
            self._wav.close()
            raise ValueError("Expected a 16 kHz 16-bit mono WAV")
        self.length = self._wav.getnframes()
        self.frames = 1 + self.length // HOP
        self.valid = self.length // HOP
        self._filters = _mel_filters()
        self._window = _window()

    def close(self) -> None:
        self._wav.close()

    def _samples(self, start: int, stop: int) -> np.ndarray:
        self._wav.setpos(start)
        return np.frombuffer(self._wav.readframes(stop - start), dtype=np.int16).astype(np.float32) / 32768.0

    def _emphasised(self, start: int, stop: int) -> np.ndarray:
        """Pre-emphasised samples start..stop, zero outside the signal (indices may be negative)."""
        out = np.zeros(stop - start, dtype=np.float32)
        first, last = max(start, 0), min(stop, self.length)
        if first >= last:
            return out
        raw = self._samples(max(first - 1, 0), last)
        if first == 0:
            emphasised = np.concatenate([raw[:1], raw[1:] - PREEMPHASIS * raw[:-1]])
        else:
            emphasised = raw[1:] - PREEMPHASIS * raw[:-1]
        out[first - start:last - start] = emphasised
        return out

    def log_mel(self, first: int, rows: int) -> np.ndarray:
        """Frames first..first+rows: rows x MELS, zero past the end."""
        out = np.zeros((rows, MELS), dtype=np.float32)
        count = min(first + rows, self.valid) - first
        if count <= 0:
            return out
        start = first * HOP - N_FFT // 2
        signal = self._emphasised(start, start + (count - 1) * HOP + N_FFT).astype(np.float64)
        windows = np.lib.stride_tricks.sliding_window_view(signal, N_FFT)[::HOP][:count] * self._window
        power = np.abs(np.fft.rfft(windows, axis=1)) ** 2
        out[:count] = np.log(power.astype(np.float32) @ self._filters.T + LOG_GUARD)
        return out


def _sigmoid(values: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-values))


class SpeakerCache:
    """The Arrival-Order Speaker Cache and the FIFO queue of the latest encoder frames (transformers' policy)."""

    def __init__(self):
        self.embeds = np.zeros((0, HIDDEN), dtype=np.float32)
        self.probs = np.zeros((0, SPEAKERS), dtype=np.float32)
        self.fifo = np.zeros((0, HIDDEN), dtype=np.float32)
        self.compressed = False

    def cached(self) -> np.ndarray:
        return np.concatenate([self.embeds, self.fifo])

    def update(self, step_embeds: np.ndarray, logits: np.ndarray, silence: np.ndarray, chunk_frames: int,
               mask: np.ndarray) -> None:
        """Pushes the chunk's frames (after the cached ones in `step_embeds`) to the FIFO; its oldest go to the cache."""
        cache_len, fifo_len = len(self.embeds), len(self.fifo)
        probs = _sigmoid(logits).reshape(-1, FACTOR, SPEAKERS).mean(axis=1) * mask[:, None]
        start = cache_len + fifo_len
        fifo = np.concatenate([self.fifo, step_embeds[start:start + chunk_frames]])
        popped = 0 if len(fifo) <= FIFO else min(max(UPDATE_PERIOD, len(fifo) - FIFO), len(fifo))
        if popped:
            fifo_probs = probs[cache_len:cache_len + len(fifo)]
            # An uncompressed cache holds plain chunk frames, re-estimated at this step;
            # a compressed one is out of order, so only its stored probabilities apply.
            stored = self.probs if self.compressed else probs[:cache_len]
            embeds = np.concatenate([self.embeds, fifo[:popped]])
            cache_probs = np.concatenate([stored, fifo_probs[:popped]])
            fifo = fifo[popped:]
            if len(embeds) > CACHE:
                embeds, cache_probs = compress(embeds, cache_probs, silence)
                self.compressed = True
            self.embeds, self.probs = embeds, cache_probs.astype(np.float32)
        self.fifo = fifo


def frame_scores(probs: np.ndarray) -> np.ndarray:
    """High for frames that clearly belong to one speaker; -inf for frames that are not that speaker's speech."""
    log_probs = np.log(np.maximum(probs, SCORE_THRESHOLD))
    log_complements = np.log(np.maximum(1.0 - probs, SCORE_THRESHOLD))
    scores = log_probs - log_complements + log_complements.sum(axis=1, keepdims=True) - math.log(0.5)
    speech = probs > 0.5
    scores[~speech] = -np.inf
    positive = scores > 0
    enough = positive.sum(axis=0) >= MIN_POSITIVE
    scores[~positive & speech & enough[None, :]] = -np.inf
    return scores


def _boost(scores: np.ndarray, count: int, amount: float) -> None:
    count = min(count, len(scores))
    top = np.argpartition(-scores, count - 1, axis=0)[:count]
    np.add.at(scores, (top, np.arange(SPEAKERS)[None, :].repeat(count, axis=0)), amount)


def compress(embeds: np.ndarray, probs: np.ndarray, silence: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Keeps the CACHE most telling frames, grouped by speaker and in their order within a speaker,
    with one slot of learned silence per speaker."""
    frames = len(embeds)
    scores = frame_scores(probs)
    scores[CACHE:] += LATEST_BOOST  # the frames just popped from the FIFO
    _boost(scores, STRONG_BOOSTED, -2.0 * math.log(0.5))
    _boost(scores, WEAK_BOOSTED, -math.log(0.5))
    scored = frames + SILENCE_FRAMES
    scores = np.concatenate([scores, np.full((SILENCE_FRAMES, SPEAKERS), np.inf)])
    flat = scores.T.reshape(-1)  # speaker-major
    sentinel = scored * SPEAKERS
    picked = np.argpartition(-flat, CACHE - 1)[:CACHE]
    picked = np.sort(np.where(flat[picked] == -np.inf, sentinel, picked))
    rows = np.where(picked == sentinel, frames, np.minimum(picked % scored, frames))
    embeds = np.concatenate([embeds, silence[None, :].astype(np.float32)])
    probs = np.concatenate([probs, np.zeros((1, SPEAKERS), dtype=probs.dtype)])
    return embeds[rows], probs[rows]


def _session():
    import onnxruntime

    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = settings.diarization_threads
    options.inter_op_num_threads = 1
    return onnxruntime.InferenceSession(str(model_path()), options, providers=["CPUExecutionProvider"])


def activity(wav_path: Path, *, on_progress: Callable[[float], None] | None = None,
             session=None, keep_probabilities: bool = False) -> np.ndarray:
    """For every 10 ms frame and speaker, whether they speak (frames x SPEAKERS, bool).

    `keep_probabilities` returns the float probabilities instead (tests)."""
    session = session or _session()
    audio = Audio(wav_path)
    try:
        steps = math.ceil(audio.frames / FACTOR)
        valid_steps = math.ceil(audio.valid / FACTOR)
        out = np.zeros((steps * FACTOR, SPEAKERS), dtype=np.float32 if keep_probabilities else bool)
        cache = SpeakerCache()
        silence = None
        for start in range(0, steps, CHUNK):
            end = min(start + CHUNK, steps)
            ahead = min(end + RIGHT_CONTEXT, steps)
            features = audio.log_mel(start * FACTOR, (ahead - start) * FACTOR)
            cached = cache.cached()
            # An encoder frame counts when its first mel frame is real audio (transformers' mask[:, ::8]).
            mask = np.concatenate([np.ones(len(cached)), np.arange(start, ahead) < valid_steps]).astype(np.int64)
            logits, chunk_embeds, silence_embeds = session.run(
                ["logits", "chunk_embeds", "silence_embeds"],
                {
                    "input_features": features[None],
                    "cached_embeds": cached[None].astype(np.float32),
                    "attention_mask": mask[None],
                },
            )
            if silence is None:
                silence = silence_embeds
            logits = logits[0]
            chunk = logits[len(cached) * FACTOR:(len(cached) + end - start) * FACTOR]
            probs = _sigmoid(chunk)
            out[start * FACTOR:end * FACTOR] = probs if keep_probabilities else probs > ACTIVE
            step_embeds = np.concatenate([cached, chunk_embeds[0]])
            cache.update(step_embeds, logits, silence, end - start, mask.astype(np.float32))
            if on_progress:
                on_progress(min(end * FACTOR, audio.frames) * FRAME_SECONDS)
        return out[:audio.frames]
    finally:
        audio.close()


def _runs(column: np.ndarray) -> list[tuple[float, float]]:
    """(start, end) in seconds of one speaker's speech: short pauses bridged, blips dropped."""
    edges = np.diff(np.concatenate([[0], column.astype(np.int8), [0]]))
    starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
    runs: list[list[float]] = []
    for a, b in zip(starts * FRAME_SECONDS, ends * FRAME_SECONDS):
        if runs and a - runs[-1][1] < BRIDGE_SECONDS:
            runs[-1][1] = b
        else:
            runs.append([a, b])
    return [(round(a, 2), round(b, 2)) for a, b in runs if b - a >= MIN_TURN_SECONDS]


def _reassign(turns: list[tuple[float, float, int]], keep: set[int]) -> list[tuple[float, float, int]]:
    """The turns of a speaker left out go to the speaker of the nearest kept turn."""
    anchors = [turn for turn in turns if turn[2] in keep]
    if not anchors:
        return turns
    result = []
    for start, end, speaker in turns:
        if speaker not in keep:
            middle = (start + end) / 2
            speaker = min(anchors, key=lambda t: t[0] - middle if middle < t[0] else max(middle - t[1], 0.0))[2]
        result.append((start, end, speaker))
    return result


def turns_from_activity(active: np.ndarray, num_speakers: int | None = None) -> list[tuple[float, float, int]]:
    """Speaker turns (start, end, model speaker), unordered; overlapping speech gives overlapping turns."""
    turns = [(a, b, speaker) for speaker in range(active.shape[1]) for a, b in _runs(active[:, speaker])]
    spoken: dict[int, float] = {}
    for start, end, speaker in turns:
        spoken[speaker] = spoken.get(speaker, 0.0) + end - start
    if num_speakers and num_speakers > 0:
        kept = sorted(spoken, key=lambda s: (-spoken[s], s))[:num_speakers]
        return _reassign(turns, set(kept))
    floor = max(MIN_SPEAKER_SECONDS, MIN_SPEAKER_SHARE * sum(spoken.values()))
    return _reassign(turns, {s for s, seconds in spoken.items() if seconds >= floor})
