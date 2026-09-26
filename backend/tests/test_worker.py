import json
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import worker
from app.llm import ChunkSummary
from app.db import Base
from app.models import ProcessingJob, Summary, SummaryTemplate, TranscriptSegment, Video


@pytest.fixture
def worker_environment(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)
    source_dir = tmp_path / "uploads"
    source_dir.mkdir()
    monkeypatch.setattr(worker, "SessionLocal", session)
    monkeypatch.setattr(worker.settings, "data_dir", tmp_path)
    return session, source_dir


def add_job(session, source: Path, *, video_status="QUEUED"):
    video_id = "video-1"
    job_id = "job-1"
    video = Video(
        id=video_id,
        filename=source.name,
        original_filename="clip.mp4",
        path=str(source),
        duration_seconds=2.0,
        size_bytes=source.stat().st_size if source.exists() else 0,
        status=video_status,
    )
    job = ProcessingJob(id=job_id, video_id=video_id, stage="QUEUED", status="QUEUED", progress=0)
    with session() as db:
        db.add(video)
        db.flush()
        db.add(job)
        db.commit()
    return video_id, job_id


def mock_external_pipeline(monkeypatch):
    def fake_ffmpeg(command, **kwargs):
        # A real 16 kHz mono WAV (2 s of silence): transcription reads it window by window.
        with wave.open(command[-1], "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16000)
            wav.writeframes(b"\x00\x00" * 32000)
        return SimpleNamespace(returncode=0)

    class FakeWhisper:
        def transcribe(self, *args, **kwargs):
            segments = [SimpleNamespace(start=0.0, end=1.0, text="Bonjour"), SimpleNamespace(start=1.0, end=2.0, text="Monde")]
            return iter(segments), SimpleNamespace(language="fr")

    monkeypatch.setattr(worker.subprocess, "run", fake_ffmpeg)
    monkeypatch.setattr(worker, "get_whisper_model", lambda: FakeWhisper())
    monkeypatch.setattr(worker, "translate_chunk", lambda text, language, **_: f"{text} ({language})")
    monkeypatch.setattr(worker, "summarize_chunk", lambda text, language, **_: ChunkSummary(f"Résumé: {text}"))
    monkeypatch.setattr(worker, "final_summary", lambda intermediate, template, language, **_: f"# Final\n{intermediate}")


def test_pipeline_completes_only_after_exports(worker_environment, monkeypatch):
    session, source_dir = worker_environment
    source = source_dir / "source.mp4"
    source.write_bytes(b"video")
    video_id, job_id = add_job(session, source)
    mock_external_pipeline(monkeypatch)

    worker.run_pipeline(job_id)

    with session() as db:
        video = db.get(Video, video_id)
        job = db.get(ProcessingJob, job_id)
        assert video.status == "COMPLETED"
        assert job.status == "COMPLETED"
        assert job.error is None
    export_dir = worker.settings.exports_dir / video_id
    assert (export_dir / "transcript.txt").read_text(encoding="utf-8") == "[00:00:00] Bonjour\n[00:00:01] Monde"
    assert (export_dir / "summary.md").exists()
    assert (export_dir / "metadata.json").exists()


def test_missing_source_is_a_stable_failure(worker_environment, caplog):
    session, source_dir = worker_environment
    source = source_dir / "missing.mp4"
    video_id, job_id = add_job(session, source)

    with pytest.raises(worker.PipelineError):
        worker.run_pipeline(job_id)

    with session() as db:
        video = db.get(Video, video_id)
        job = db.get(ProcessingJob, job_id)
        assert video.status == "FAILED"
        assert job.status == "FAILED"
        assert job.error == worker.ERROR_SOURCE_NOT_FOUND
        assert "Traceback" not in job.error
    assert "Video processing failed" in caplog.text


def test_export_failure_does_not_leave_video_completed(worker_environment, monkeypatch):
    session, source_dir = worker_environment
    source = source_dir / "source.mp4"
    source.write_bytes(b"video")
    video_id, job_id = add_job(session, source)
    mock_external_pipeline(monkeypatch)
    original_write_text = Path.write_text

    def fail_summary_export(path, *args, **kwargs):
        if path.name == "summary.md":
            raise OSError("disk full")
        return original_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_summary_export)
    with pytest.raises(OSError):
        worker.run_pipeline(job_id)

    with session() as db:
        video = db.get(Video, video_id)
        job = db.get(ProcessingJob, job_id)
        assert video.status == "FAILED"
        assert job.status == "FAILED"
        assert job.error == worker.ERROR_PROCESSING_FAILED
        assert "disk full" not in job.error


def test_terminal_job_cannot_be_mutated(worker_environment):
    session, source_dir = worker_environment
    source = source_dir / "source.mp4"
    source.write_bytes(b"video")
    _, job_id = add_job(session, source)
    with session() as db:
        job = db.get(ProcessingJob, job_id)
        job.status = "COMPLETED"
        job.stage = "COMPLETED"
        job.progress = 100
        job.error = None
        db.commit()

    assert worker.set_job(job_id, stage="FAILED", status="FAILED", progress=0, error="should not change") is False
    with session() as db:
        job = db.get(ProcessingJob, job_id)
        assert job.status == "COMPLETED"
        assert job.stage == "COMPLETED"
        assert job.progress == 100
        assert job.error is None


def test_video_status_transitions_are_enforced(worker_environment):
    session, source_dir = worker_environment
    source = source_dir / "source.mp4"
    source.write_bytes(b"video")
    video_id, _ = add_job(session, source)

    # A queued video cannot skip processing, but can enter processing.
    assert worker.set_video_status(video_id, "COMPLETED") is False
    with session() as db:
        assert db.get(Video, video_id).status == "QUEUED"
    assert worker.set_video_status(video_id, "PROCESSING") is True
    assert worker.set_video_status(video_id, "COMPLETED") is True


def test_terminal_video_cannot_be_mutated(worker_environment):
    session, source_dir = worker_environment
    source = source_dir / "source.mp4"
    source.write_bytes(b"video")
    video_id, _ = add_job(session, source, video_status="COMPLETED")

    assert worker.set_video_status(video_id, "FAILED") is False
    assert worker.set_video_status(video_id, "PROCESSING") is False
    with session() as db:
        assert db.get(Video, video_id).status == "COMPLETED"


def test_failed_pipeline_keeps_job_and_video_terminal_together(worker_environment):
    session, source_dir = worker_environment
    source = source_dir / "missing.mp4"
    video_id, job_id = add_job(session, source)

    with pytest.raises(worker.PipelineError):
        worker.run_pipeline(job_id)

    with session() as db:
        video = db.get(Video, video_id)
        job = db.get(ProcessingJob, job_id)
        assert (video.status, job.status) == ("FAILED", "FAILED")


def test_interrupted_job_becomes_failed_and_deletable(worker_environment):
    session, source_dir = worker_environment
    source = source_dir / "source.mp4"
    source.write_bytes(b"video")
    video_id, job_id = add_job(session, source)
    assert worker.mark_interrupted_job(job_id) is True

    with session() as db:
        video = db.get(Video, video_id)
        job = db.get(ProcessingJob, job_id)
        assert (video.status, job.status, job.stage) == ("FAILED", "FAILED", "FAILED")
        assert job.error == worker.ERROR_PROCESSING_INTERRUPTED
        assert job.finished_at is not None
    assert worker.mark_interrupted_job(job_id) is False


def test_worker_startup_recovers_running_job(worker_environment):
    session, source_dir = worker_environment
    source = source_dir / "source.mp4"
    source.write_bytes(b"video")
    video_id, job_id = add_job(session, source)
    assert worker._start_job(job_id)[0] is True

    assert worker.recover_interrupted_jobs() == 1
    with session() as db:
        assert db.get(ProcessingJob, job_id).status == "FAILED"
        assert db.get(Video, video_id).status == "FAILED"
    assert worker.recover_interrupted_jobs() == 0


def test_retry_reuses_a_persisted_transcript(worker_environment, monkeypatch):
    session, source_dir = worker_environment
    source = source_dir / "source.mp4"
    source.write_bytes(b"video")
    video_id, failed_job_id = add_job(session, source, video_status="FAILED")
    with session() as db:
        failed_job = db.get(ProcessingJob, failed_job_id)
        failed_job.status = "FAILED"
        failed_job.stage = "FAILED"
        video = db.get(Video, video_id)
        video.transcript_text = "[00:00:00] Bonjour"
        video.detected_language = "fr"
        video.status = "QUEUED"
        db.add(TranscriptSegment(video_id=video_id, start_seconds=0.0, end_seconds=1.0, text="Bonjour"))
        db.add(ProcessingJob(id="job-retry", video_id=video_id, stage="QUEUED", status="QUEUED", progress=0))
        db.commit()

    mock_external_pipeline(monkeypatch)
    monkeypatch.setattr(
        worker.subprocess,
        "run",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("ffmpeg must not run on retry")),
    )
    monkeypatch.setattr(
        worker,
        "get_whisper_model",
        lambda: (_ for _ in ()).throw(AssertionError("Whisper must not run on retry")),
    )

    worker.run_pipeline("job-retry")

    with session() as db:
        assert db.get(Video, video_id).status == "COMPLETED"
        assert db.get(ProcessingJob, "job-retry").status == "COMPLETED"


def test_missing_job_is_ignored(worker_environment):
    worker.run_pipeline("does-not-exist")


def configure_job(session, **changes):
    with session() as db:
        video = db.get(Video, "video-1")
        job = db.get(ProcessingJob, "job-1")
        for key, value in changes.items():
            setattr(job if hasattr(ProcessingJob, key) and key != "id" else video, key, value)
        db.commit()


def test_template_and_instructions_are_sent_separately(worker_environment, monkeypatch):
    session, source_dir = worker_environment
    source = source_dir / "clip.mp4"
    source.write_bytes(b"video")
    add_job(session, source)
    with session() as db:
        db.add(SummaryTemplate(id="tpl", name="Cours", prompt="# Notions\nDéfinitions courtes.", is_default=False))
        db.commit()
    configure_job(session, template_id="tpl", custom_prompt="Rédige au tutoiement.", summary_length="detailed")
    mock_external_pipeline(monkeypatch)
    calls = {}

    def fake_final(intermediate, template, language, **kwargs):
        calls.update(template=template, language=language, **kwargs)
        return "# Notions\nOk."

    monkeypatch.setattr(worker, "final_summary", fake_final)

    worker.run_pipeline("job-1")

    assert calls["template"] == "# Notions\nDéfinitions courtes."
    assert calls["instructions"] == "Rédige au tutoiement."
    assert calls["language"] == "français"  # Whisper's "fr" as a language name
    assert calls["word_budget"] == 500  # detailed floor for a 2-second clip
    with session() as db:
        summary = db.query(Summary).one()
        assert (summary.template_id, summary.summary_length) == ("tpl", "detailed")
    metadata = json.loads((worker.settings.exports_dir / "video-1" / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["summary_length"] == "detailed"
    assert metadata["word_budget"] == 500
    assert metadata["template_name"] == "Cours"


def test_forced_language_and_vocabulary_reach_whisper_and_prompts(worker_environment, monkeypatch):
    session, source_dir = worker_environment
    source = source_dir / "clip.mp4"
    source.write_bytes(b"video")
    add_job(session, source)
    configure_job(
        session, detected_language="en", source_language_forced=True,
        vocabulary="Okr\nAlpha", glossary_snapshot="OKR\nDoñana",
    )
    mock_external_pipeline(monkeypatch)
    whisper_kwargs, prompt_vocabularies = {}, []

    class FakeWhisper:
        def transcribe(self, *args, **kwargs):
            whisper_kwargs.update(kwargs)
            return iter([SimpleNamespace(start=0.0, end=1.0, text="Hello")]), SimpleNamespace(language="fr")

    monkeypatch.setattr(worker, "get_whisper_model", lambda: FakeWhisper())
    monkeypatch.setattr(worker, "summarize_chunk", lambda text, language, **kw: prompt_vocabularies.append(kw["vocabulary"]) or ChunkSummary("- ok"))

    worker.run_pipeline("job-1")

    assert whisper_kwargs["language"] == "en"
    assert whisper_kwargs["initial_prompt"] == "Termes : Okr, Alpha, Doñana."
    assert prompt_vocabularies == [["Okr", "Alpha", "Doñana"]]
    with session() as db:
        assert db.get(Video, "video-1").detected_language == "en"  # not Whisper's guess


def test_long_videos_are_reduced_in_groups_before_the_final_summary(worker_environment, monkeypatch):
    session, source_dir = worker_environment
    source = source_dir / "clip.mp4"
    source.write_bytes(b"video")
    add_job(session, source)
    mock_external_pipeline(monkeypatch)

    class ManySegments:
        def transcribe(self, *args, **kwargs):
            # Long lines of varied words (a word group repeated in a row is a Whisper loop, collapsed since phase 2).
            segments = [SimpleNamespace(start=float(i), end=float(i + 1), text=f"Phrase {i} : " + " ".join(f"point{k}" for k in range(30)))
                        for i in range(40)]
            return iter(segments), SimpleNamespace(language="fr")

    monkeypatch.setattr(worker, "get_whisper_model", lambda: ManySegments())
    monkeypatch.setattr(worker.settings, "summary_chunk_chars", 300)
    monkeypatch.setattr(worker.settings, "llm_num_ctx", 2048)
    monkeypatch.setattr(worker, "summarize_chunk", lambda text, language, **_: ChunkSummary("- " + "point important " * 30))
    groups = []
    monkeypatch.setattr(worker, "summarize_group", lambda text, language, **_: groups.append(text) or "- fusion")
    stages = []
    real_set_job = worker.set_job
    monkeypatch.setattr(worker, "set_job", lambda job_id, **kw: stages.append(kw.get("stage")) or real_set_job(job_id, **kw))
    final_inputs = []
    monkeypatch.setattr(worker, "final_summary", lambda intermediate, *a, **k: final_inputs.append(intermediate) or "# Final")

    worker.run_pipeline("job-1")

    assert "SUMMARIZING_GROUPS" in stages
    # Intermediate summaries are labelled by their time span, never "Bloc"/"Partie".
    assert groups and groups[0].startswith("### [00:00:00] → [")
    assert final_inputs[0].startswith("### [00:00:00] → [")
    assert "Bloc" not in final_inputs[0] and "Partie" not in final_inputs[0]
    assert worker.estimated_tokens(final_inputs[0]) <= worker.final_input_budget(worker.final_output_tokens(500))


def test_reduce_keeps_order_and_always_shrinks():
    blocks = [f"### Bloc {i}\n" + "x" * 700 for i in range(9)]
    merged_inputs = []

    def fake_group(text, language, **_):
        merged_inputs.append(text)
        return "- court"

    original = worker.summarize_group
    worker.summarize_group = fake_group
    try:
        result = worker.reduce_block_summaries(blocks, "français", budget_tokens=500, detailed=False, vocabulary=[])
    finally:
        worker.summarize_group = original
    assert worker.estimated_tokens("\n\n".join(result)) <= 500
    assert merged_inputs[0].startswith("### Bloc 0") and "### Bloc 1" in merged_inputs[0]


def test_video_without_speech_gets_a_fixed_summary(worker_environment, monkeypatch):
    session, source_dir = worker_environment
    source = source_dir / "clip.mp4"
    source.write_bytes(b"video")
    add_job(session, source)
    mock_external_pipeline(monkeypatch)

    class Silent:
        def transcribe(self, *args, **kwargs):
            return iter([]), SimpleNamespace(language="fr")

    monkeypatch.setattr(worker, "get_whisper_model", lambda: Silent())
    monkeypatch.setattr(worker, "final_summary", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no LLM call")))

    worker.run_pipeline("job-1")

    with session() as db:
        assert db.query(Summary).one().content_markdown == worker.NO_SPEECH_SUMMARY
        assert db.get(Video, "video-1").status == "COMPLETED"


def test_whisper_is_released_before_the_summary(worker_environment, monkeypatch):
    session, source_dir = worker_environment
    source = source_dir / "clip.mp4"
    source.write_bytes(b"video")
    add_job(session, source)
    mock_external_pipeline(monkeypatch)
    events = []

    class Unloadable:
        def unload_model(self):
            events.append("unloaded")

    class FakeWhisper:
        model = Unloadable()

        def transcribe(self, *args, **kwargs):
            return iter([SimpleNamespace(start=0.0, end=1.0, text="Bonjour")]), SimpleNamespace(language="fr")

    monkeypatch.setattr(worker, "_whisper_model", FakeWhisper())
    monkeypatch.setattr(worker, "get_whisper_model", lambda: worker._whisper_model)
    monkeypatch.setattr(worker, "summarize_chunk", lambda text, language, **_: events.append("summary") or ChunkSummary("- ok"))

    worker.run_pipeline("job-1")

    assert events == ["unloaded", "summary"]
    assert worker._whisper_model is None
