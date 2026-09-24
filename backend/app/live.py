"""The `live` service: running transcript of the recordings being made (n°11).

The browser uploads its recording in chunks of a few seconds (n°10). For each
recording in progress with `live` set, this service feeds the new bytes to one
ffmpeg process per recording (the chunks form a single WebM/Ogg stream), which
decodes them into 16 kHz PCM. The audio not transcribed yet is sent to a small
Whisper model every few seconds, with word timings. The words up to the last
sentence end are kept, one line per sentence; the words of the last two
seconds, or of an unfinished sentence, are transcribed again with what follows
(a small model often returns several sentences as one segment: keeping whole
segments delayed the preview by 25 s). The lines appear in `live_segments`.

This transcript is a preview. Once the recording stops, the usual pipeline
transcribes the whole file with the main model, then summarizes it.
"""
import logging
import signal
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
from redis import Redis
from sqlalchemy import func, select

from .analysis_options import whisper_initial_prompt
from .config import settings
from .db import SessionLocal, engine
from .models import GlossaryTerm, LiveSegment, Recording
from .schema import assert_schema_current
from .status import LIVE_HEARTBEAT_KEY

logger = logging.getLogger(__name__)

RATE = 16000
# New audio needed before another pass: shorter reacts faster, but costs more passes.
MIN_NEW_SECONDS = 3.0
# Past this, everything is kept even if the last sentence is cut: the preview must move on.
MAX_WINDOW_SECONDS = 24.0
MIN_WINDOW_SECONDS = 1.0
# Words ending this close to the end of the audio are transcribed again on the next pass.
STABLE_MARGIN_SECONDS = 2.0
SENTENCE_ENDS = (".", "?", "!", "…")
PROMPT_TAIL_CHARS = 200
READ_LIMIT_BYTES = 4 * 1024 * 1024
MODEL_IDLE_SECONDS = 120


class LiveDecoder:
    """One ffmpeg process turning a growing WebM/Ogg/MP4 stream into 16 kHz mono PCM."""

    def __init__(self):
        self.process = subprocess.Popen(
            ["ffmpeg", "-loglevel", "error", "-nostdin", "-i", "pipe:0", "-vn", "-ac", "1", "-ar", str(RATE),
             "-f", "s16le", "pipe:1"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        self._pcm = bytearray()
        self._lock = threading.Lock()
        self._reader = threading.Thread(target=self._read, name="live-decoder", daemon=True)
        self._reader.start()

    def _read(self) -> None:
        while True:
            data = self.process.stdout.read1(65536) if hasattr(self.process.stdout, "read1") else self.process.stdout.read(65536)
            if not data:
                return
            with self._lock:
                self._pcm.extend(data)

    def feed(self, data: bytes) -> None:
        if data:
            self.process.stdin.write(data)
            self.process.stdin.flush()

    def take(self) -> bytes:
        """PCM decoded since the last call (whole 16-bit samples only)."""
        with self._lock:
            usable = len(self._pcm) - len(self._pcm) % 2
            data = bytes(self._pcm[:usable])
            del self._pcm[:usable]
        return data

    def finish(self, timeout: float = 10) -> bytes:
        """End of stream: flush what ffmpeg still holds."""
        try:
            self.process.stdin.close()
        except OSError:
            pass
        self.process.wait(timeout=timeout)
        self._reader.join(timeout=timeout)
        return self.take()

    def close(self) -> None:
        try:
            self.process.stdin.close()
        except OSError:
            pass
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)


class LiveTranscriber:
    """The audio not committed yet, and when to transcribe it (pure logic, no I/O)."""

    def __init__(self, *, committed: float = 0.0, language: str | None = None, vocabulary_prompt: str | None = None):
        self.samples = np.zeros(0, dtype=np.int16)
        # Time of samples[0]: the audio before `committed` is dropped as it goes.
        self.base = 0.0
        self.received = 0.0
        self.committed = committed
        self.attempted_until = committed
        self.language = language
        self.vocabulary_prompt = vocabulary_prompt
        self.previous_text = ""

    @property
    def end(self) -> float:
        return self.base + len(self.samples) / RATE

    def add(self, pcm: bytes) -> None:
        """Append decoded audio; what precedes `committed` (a restart) is skipped."""
        if not pcm:
            return
        new = np.frombuffer(pcm, dtype=np.int16)
        self.received += len(new) / RATE
        self.samples = np.concatenate([self.samples, new])
        self._trim()

    def _trim(self) -> None:
        drop = int((self.committed - self.base) * RATE)
        if drop > 0:
            drop = min(drop, len(self.samples))
            self.samples = self.samples[drop:]
            self.base += drop / RATE

    def _prompt(self) -> str | None:
        parts = [part for part in (self.vocabulary_prompt, self.previous_text[-PROMPT_TAIL_CHARS:]) if part]
        return " ".join(parts) or None

    def due(self) -> bool:
        return self.end - self.attempted_until >= MIN_NEW_SECONDS and self.end - self.committed >= MIN_WINDOW_SECONDS

    def step(self, model, *, final: bool = False) -> list[tuple[float, float, str]]:
        """One pass on the pending audio; returns the segments now kept."""
        if not final and not self.due():
            return []
        if self.end - self.committed < MIN_WINDOW_SECONDS:
            return []
        start = self.committed
        offset = max(0, int((start - self.base) * RATE))
        audio = self.samples[offset:].astype(np.float32) / 32768.0
        segments, info = model.transcribe(
            audio, language=self.language, beam_size=1, vad_filter=True, word_timestamps=True,
            initial_prompt=self._prompt(), condition_on_previous_text=False,
        )
        words = _words(segments, start)
        self.attempted_until = self.end
        if words and self.language is None:
            self.language = getattr(info, "language", None)
        window = self.end - start
        # Words ending in the last seconds may still change with what follows.
        stable = self.end if final else self.end - STABLE_MARGIN_SECONDS
        eligible = [word for word in words if word[1] <= stable]
        cut = max((index for index, word in enumerate(eligible) if word[2].rstrip().endswith(SENTENCE_ENDS)), default=None)
        if cut is None and eligible and (final or window >= MAX_WINDOW_SECONDS):
            cut = len(eligible) - 1  # no sentence end for too long: the preview must move on
        kept = _sentences(eligible[:cut + 1]) if cut is not None else []
        if kept:
            self.committed = min(kept[-1][1], self.end)
            self.previous_text = f"{self.previous_text} {' '.join(text for _, _, text in kept)}".strip()
        elif not words and window >= MAX_WINDOW_SECONDS:
            # Silence: move on, keeping a second in case a word starts there.
            self.committed = self.end - 1.0
        self._trim()
        return kept


def _words(segments, offset: float) -> list[tuple[float, float, str]]:
    """(start, end, text) of each word; a segment without word timings counts as one word."""
    words = []
    for segment in segments:
        timed = getattr(segment, "words", None)
        if timed:
            words.extend((offset + float(w.start), offset + float(w.end), w.word) for w in timed if w.word.strip())
        elif segment.text.strip():
            words.append((offset + float(segment.start), offset + float(segment.end), f" {segment.text.strip()}"))
    return words


def _sentences(words: list[tuple[float, float, str]]) -> list[tuple[float, float, str]]:
    """Words grouped into one line per sentence."""
    lines, current = [], []
    for word in words:
        current.append(word)
        if word[2].rstrip().endswith(SENTENCE_ENDS):
            lines.append(current)
            current = []
    if current:
        lines.append(current)
    return [(line[0][0], line[-1][1], "".join(word[2] for word in line).strip()) for line in lines]


class Session:
    def __init__(self, recording: Recording, committed: float, vocabulary_prompt: str | None):
        self.id = recording.id
        self.path = Path(recording.path)
        self.offset = 0
        self.decoder = LiveDecoder()
        self.transcriber = LiveTranscriber(committed=committed, language=recording.language, vocabulary_prompt=vocabulary_prompt)

    def pump(self) -> None:
        """Feed the bytes written since the last call to the decoder."""
        try:
            with self.path.open("rb") as source:
                source.seek(self.offset)
                data = source.read(READ_LIMIT_BYTES)
        except FileNotFoundError:
            return
        self.offset += len(data)
        self.decoder.feed(data)
        self.transcriber.add(self.decoder.take())

    def close(self) -> None:
        try:
            self.decoder.close()
        except Exception:
            logger.warning("Unable to stop the decoder of %s", self.id, exc_info=True)


class LiveService:
    def __init__(self, model_factory=None, clock=time.monotonic):
        self.sessions: dict[str, Session] = {}
        self.model = None
        self.model_factory = model_factory or self._load_model
        self.clock = clock
        self.idle_since = clock()

    @staticmethod
    def _load_model():
        from faster_whisper import WhisperModel

        logger.info("Loading the live model %s on %s", settings.live_whisper_model, settings.whisper_device)
        return WhisperModel(settings.live_whisper_model, device=settings.whisper_device, compute_type=settings.whisper_compute_type)

    def _open(self, recording: Recording) -> Session:
        with SessionLocal() as db:
            committed = db.scalar(select(func.max(LiveSegment.end_seconds)).where(LiveSegment.recording_id == recording.id)) or 0.0
            glossary = list(db.scalars(select(GlossaryTerm.term).order_by(GlossaryTerm.position)))
        return Session(recording, committed, whisper_initial_prompt(glossary))

    def tick(self) -> int:
        """One pass over the recordings in progress; returns the lines added."""
        with SessionLocal() as db:
            active = list(db.scalars(select(Recording).where(Recording.status == "RECORDING", Recording.live.is_(True))))
        active_ids = {recording.id for recording in active}
        for session_id in list(self.sessions):
            if session_id not in active_ids:
                self.sessions.pop(session_id).close()
        added = 0
        for recording in active:
            try:
                session = self.sessions.get(recording.id)
                if session is None:
                    session = self.sessions[recording.id] = self._open(recording)
                session.pump()
                if not session.transcriber.due():
                    continue
                if self.model is None:
                    self.model = self.model_factory()
                rows = session.transcriber.step(self.model)
                if rows:
                    with SessionLocal() as db:
                        db.add_all([LiveSegment(recording_id=recording.id, start_seconds=s, end_seconds=e, text=t) for s, e, t in rows])
                        db.commit()
                    added += len(rows)
            except Exception as exc:
                logger.exception("Live transcript of %s failed", recording.id)
                self._fail(recording.id, exc)
        if self.sessions:
            self.idle_since = self.clock()
        elif self.model is not None and self.clock() - self.idle_since > MODEL_IDLE_SECONDS:
            # Free the memory (GPU included) between two recordings.
            self.model = None
        return added

    def _fail(self, recording_id: str, exc: Exception) -> None:
        session = self.sessions.pop(recording_id, None)
        if session:
            session.close()
        with SessionLocal() as db:
            recording = db.get(Recording, recording_id)
            if recording:
                recording.live = False
                recording.live_error = "La transcription en direct s'est arrêtée ; l'enregistrement continue et sera transcrit à la fin"
                db.commit()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    assert_schema_current(engine)
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    service = LiveService()
    stopping = False

    def stop(*_):
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info("Live service started (model %s, %s)", settings.live_whisper_model, settings.whisper_device)
    while not stopping:
        try:
            redis.set(LIVE_HEARTBEAT_KEY, str(time.time()), ex=30)
        except Exception:
            logger.warning("Unable to write the live heartbeat", exc_info=True)
        try:
            service.tick()
        except Exception:
            logger.exception("Live tick failed")
        time.sleep(settings.live_poll_seconds)
    for session in service.sessions.values():
        session.close()


if __name__ == "__main__":
    main()
