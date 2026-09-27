"""Two sides of a recorded call (feuille de route n° 3, phase 4): echo, merge, labels, pipeline, recordings."""
import wave
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main, sides, worker
from app.db import Base
from app.diarization import Turn
from app.llm import ChunkSummary
from app.models import ProcessingJob, Recording, Speaker, TranscriptSegment, Video

RATE = 16000


def write_wav(path: Path, *channels: np.ndarray) -> Path:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(len(channels))
        wav.setsampwidth(2)
        wav.setframerate(RATE)
        wav.writeframes((np.stack(channels, axis=1) * 32767).astype(np.int16).tobytes())
    return path


def burst(seconds: float, *spans: tuple[float, float, float]) -> np.ndarray:
    """A voice-like tone over each (start, end, amplitude)."""
    track = np.zeros(int(seconds * RATE), dtype=np.float32)
    t = np.arange(len(track)) / RATE
    for start, end, amplitude in spans:
        part = (t >= start) & (t < end)
        track[part] = amplitude * np.sin(2 * np.pi * 220 * t[part]) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t[part]))
    return track


def call(tmp_path: Path) -> Path:
    """You speak at 0.5-2 s; they speak at 3.5-5 s, and come back into your microphone at a fifth of their level."""
    theirs = burst(6, (3.5, 5.0, 0.5))
    mine = burst(6, (0.5, 2.0, 0.3))
    mine[int(0.05 * RATE):] += 0.2 * theirs[:-int(0.05 * RATE)]
    return write_wav(tmp_path / "call.wav", mine, theirs)


# --- echo by level -----------------------------------------------------------------------

def test_your_voice_is_kept_and_their_echo_silenced():
    frames = 100
    other = np.zeros(frames)
    other[50:80] = 0.2
    mic = np.zeros(frames)
    mic[10:30] = 0.1  # you
    mic[51:81] = 0.04  # their echo, a fifth of their level
    kept = sides.own_voice(mic, other)
    assert kept[10:30].all() and not kept[40:].any()


def test_the_echo_of_someone_who_barely_speaks_is_not_taken_for_their_voice():
    other = np.zeros(100)
    other[20:80] = 0.2
    mic = np.zeros(100)
    mic[5:12] = 0.1  # a few words of yours
    mic[21:81] = 0.04  # then their echo, for a long time
    # Levelled over all its sound, the microphone would have been mostly echo, raised to speaking level.
    kept = sides.own_voice(mic, other)
    assert kept[5:12].all() and not kept[20:].any()


def test_speaking_at_the_same_time_keeps_your_voice():
    other = np.zeros(100)
    other[20:60] = 0.1
    mic = np.zeros(100)
    mic[30:50] = 0.1  # as loud as them, while they talk
    assert sides.own_voice(mic, other)[30:50].all()


def test_short_blips_are_not_speech():
    mic = np.zeros(100)
    mic[10:12] = 0.1  # 60 ms
    mic[40:60] = 0.1
    kept = sides.own_voice(mic, np.zeros(100))
    assert not kept[10:12].any() and kept[40:60].all()


def test_regions_are_padded_and_joined():
    active = np.zeros(200, dtype=bool)
    active[10:20] = True
    active[35:40] = True  # 450 ms later: joined
    active[150:160] = True
    pad = int(sides.PAD_SECONDS * RATE)
    assert sides.regions(active) == [(10 * sides.FRAME - pad, 40 * sides.FRAME + pad), (150 * sides.FRAME - pad, 160 * sides.FRAME + pad)]


def test_prepared_sides_split_the_channels_and_silence_the_echo(tmp_path, monkeypatch):
    monkeypatch.setattr(sides.settings, "data_dir", tmp_path)
    source = call(tmp_path)
    assert sides.is_two_sided(source)
    assert not sides.is_two_sided(write_wav(tmp_path / "mono.wav", burst(1, (0, 1, 0.3))))
    with sides.prepared(source) as prepared:
        you = sides.frame_levels(prepared.you)
        others = sides.frame_levels(prepared.others)
        folder = prepared.you.parent
        assert abs(prepared.duration - 6) < 0.01
        assert 1.2 < prepared.you_seconds < 2.2 and 1.2 < prepared.others_seconds < 2.0
    second = lambda seconds: int(seconds / 0.03)  # noqa: E731
    assert you[second(0.6):second(1.9)].min() > 0.02  # your voice
    assert you[second(3.6):second(5.0)].max() == 0  # their echo, silenced
    assert others[second(3.6):second(4.9)].min() > 0.05 and others[second(0.6):second(1.9)].max() == 0
    assert not folder.exists()


# --- echo by text, merge -----------------------------------------------------------------

def test_a_line_repeating_the_other_side_at_the_same_moment_is_echo():
    theirs = sides.OtherSide([(10.0, 14.0, "Les maquettes sont validées, il reste les photos.")])
    assert sides.is_echo((10.2, 14.1, "les maquettes sont validees il reste des photos"), theirs)
    assert not sides.is_echo((30.0, 33.0, "Les maquettes sont validées, il reste les photos."), theirs)  # much later
    assert not sides.is_echo((11.0, 13.0, "Parfait, je valide les photos demain."), theirs)  # your own words
    assert sides.is_echo((12.0, 12.5, "Photos."), sides.OtherSide([(11.0, 13.0, "Les photos.")]))  # word for word


def test_both_sides_are_merged_in_order_without_the_echo():
    rows = sides.merge(
        you=[(0.5, 2.0, "Bonjour à tous."), (3.6, 5.0, "Merci pour ce point sur le budget.")],
        others=[(3.5, 5.0, "Merci pour ce point sur le budget."), (2.5, 3.0, "Bonjour.")],
        you_doubts=[[[0, 3, 40]], []],
    )
    assert [(row[0], row[3]) for row in rows] == [(0.5, "you"), (2.5, "others"), (3.5, "others")]
    assert rows[0][4] == [[0, 3, 40]]


def test_a_line_is_cut_at_a_long_silence_between_two_words():
    from app.transcription import split_at_pauses

    word = lambda start, end, text, p=0.9: SimpleNamespace(start=start, end=end, word=text, probability=p)  # noqa: E731
    segment = SimpleNamespace(start=41.0, end=52.3, words=[
        word(41.0, 41.4, " Très"), word(41.4, 41.8, " bien."), word(50.9, 51.4, " Je", 0.2), word(51.4, 52.3, " leur écris."),
    ])
    pieces = split_at_pauses(segment, "Très bien. Je leur écris.", 1.5)
    assert [(start, end, text) for start, end, text, _ in pieces] == [(41.0, 41.8, "Très bien."), (50.9, 52.3, "Je leur écris.")]
    assert [w.word for w in pieces[1][3].words] == [" Je", " leur écris."]
    # No threshold, or no words: the line as it is.
    assert len(split_at_pauses(segment, "Très bien. Je leur écris.", None)) == 1
    assert split_at_pauses(SimpleNamespace(start=0, end=1), "Oui.", 1.5)[0][:3] == (0.0, 1.0, "Oui.")


# --- labels ------------------------------------------------------------------------------

def test_side_labels():
    assert Speaker(position=1, side="you").label == "Vous"
    assert Speaker(position=2, side="you", side_position=2).label == "Vous 2"
    assert Speaker(position=3, side="others").label == "Participants"
    assert Speaker(position=3, side="others", side_position=1).label == "Participant 1"
    assert Speaker(position=3, side="others", side_position=1, name="Pierre").label == "Pierre"
    assert Speaker(position=4).label == "Intervenant 4"


# --- pipeline ----------------------------------------------------------------------------

@pytest.fixture
def environment(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sides.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", session)
    monkeypatch.setattr(main, "SessionLocal", session)
    monkeypatch.setattr(worker.settings, "data_dir", tmp_path)
    monkeypatch.setattr(worker, "translate_chunk", lambda text, language, **_: text)
    monkeypatch.setattr(worker, "summarize_chunk", lambda text, language, **_: ChunkSummary(f"Résumé: {text}"))
    monkeypatch.setattr(worker, "final_summary", lambda intermediate, template, language, **_: f"# Final\n{intermediate}")
    monkeypatch.setattr(worker, "_index_after_pipeline", lambda *_: None)
    return session


class HearsTones:
    """A Whisper that writes a line over each stretch of sound: what is said depends on when."""

    def transcribe(self, audio, **kwargs):
        frame = 480
        loud = np.abs(np.pad(audio, (0, -len(audio) % frame))).reshape(-1, frame).max(axis=1) > 0.01
        edges = np.diff(np.concatenate([[0], loud.astype(np.int8), [0]]))
        segments = []
        for start, end in zip(np.flatnonzero(edges == 1) * frame / RATE, np.flatnonzero(edges == -1) * frame / RATE):
            if end - start > 0.3:
                text = "Bonjour, je présente le budget." if start < 3 else "Merci pour ce point budget."
                segments.append(SimpleNamespace(start=float(start), end=float(end), text=text, words=[], no_speech_prob=0.0))
        return iter(segments), SimpleNamespace(language="fr")


def add_video(session, source: Path, *, diarize=False, layout="sides") -> tuple[str, str]:
    with session() as db:
        db.add(Video(id="v", filename=source.name, original_filename="Appel.ogg", path=str(source), duration_seconds=6,
                     size_bytes=1, status="QUEUED", audio_layout=layout, diarize=diarize))
        db.flush()
        db.add(ProcessingJob(id="j", video_id="v", stage="QUEUED", status="QUEUED", progress=0))
        db.commit()
    return "v", "j"


def test_a_two_sided_recording_is_transcribed_side_by_side(environment, tmp_path, monkeypatch):
    video_id, job_id = add_video(environment, call(tmp_path))
    monkeypatch.setattr(worker, "get_whisper_model", lambda: HearsTones())
    worker.run_pipeline(job_id)
    with environment() as db:
        video = db.get(Video, video_id)
        assert video.status == "COMPLETED"
        lines = [(segment.side, segment.text) for segment in video.segments]
        # Your line on your side; their line once, on theirs: the echo in your microphone is gone.
        assert lines == [("you", "Bonjour, je présente le budget."), ("others", "Merci pour ce point budget.")]
        assert [speaker.label for speaker in video.speakers] == ["Vous", "Participants"]
        assert video.transcript_text.splitlines() == [
            "[00:00:00] Vous : Bonjour, je présente le budget.", "[00:00:03] Participants : Merci pour ce point budget.",
        ]


def test_voices_are_told_apart_within_each_side(environment, tmp_path, monkeypatch):
    video_id, job_id = add_video(environment, call(tmp_path), diarize=True)
    monkeypatch.setattr(worker, "get_whisper_model", lambda: HearsTones())
    heard = []

    def fake_diarize(path, *, num_speakers=None, on_progress=None):
        heard.append(path.name)
        # Your side: one voice; theirs: two, the second from 4.4 s.
        return [Turn(0.4, 2.1, 1)] if path.name == "you.wav" else [Turn(3.4, 4.4, 1), Turn(4.4, 5.1, 2)]

    monkeypatch.setattr(worker, "diarize", fake_diarize)
    worker.run_pipeline(job_id)
    assert heard == ["you.wav", "others.wav"]
    with environment() as db:
        video = db.get(Video, video_id)
        assert [speaker.label for speaker in video.speakers] == ["Vous", "Participant 1"]
        assert video.diarization_error is None


def test_a_mono_file_marked_two_sided_is_processed_as_usual(environment, tmp_path, monkeypatch):
    video_id, job_id = add_video(environment, write_wav(tmp_path / "mono.wav", burst(6, (0.5, 2.0, 0.3))))
    monkeypatch.setattr(worker, "get_whisper_model", lambda: HearsTones())
    worker.run_pipeline(job_id)
    with environment() as db:
        video = db.get(Video, video_id)
        assert video.status == "COMPLETED" and not video.speakers
        assert [segment.side for segment in video.segments] == [None]


def test_rediarizing_needs_the_stereo_source(environment, tmp_path):
    with environment() as db:
        db.add(Video(id="v", filename="x.ogg", original_filename="x.ogg", path=str(tmp_path / "gone.ogg"),
                     duration_seconds=6, size_bytes=1, status="COMPLETED", audio_layout="sides"))
        db.flush()
        db.add(TranscriptSegment(video_id="v", start_seconds=0, end_seconds=1, text="Bonjour", side="you"))
        db.commit()
    with pytest.raises(worker.PipelineError, match="supprimé"):
        worker.diarize_video("v")


# --- recordings --------------------------------------------------------------------------

def test_a_two_sided_recording_becomes_a_two_sided_video(environment, tmp_path, monkeypatch):
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)
    queued = {}
    monkeypatch.setattr(main, "create_import", lambda destination, name, options, video_id, layout=None: queued.update(
        layout=layout) or SimpleNamespace())
    monkeypatch.setattr(main, "_remux_recording", lambda source, destination: destination.write_bytes(source.read_bytes()))
    client = TestClient(main.app)
    created = client.post("/recordings", json={"title": "Appel", "mime_type": "audio/webm;codecs=opus", "sides": True}).json()
    assert created["sides"] is True
    client.put(f"/recordings/{created['id']}/chunks/0", content=b"opus")
    with environment() as db:
        assert db.get(Recording, created["id"]).sides
    try:
        client.post(f"/recordings/{created['id']}/finish", json={})
    except Exception:
        pass  # the stub job is not a JobOut; the layout handed over is what counts
    assert queued["layout"] == "sides"
