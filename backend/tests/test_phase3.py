"""Phase 3: queue estimates, cancellation, library filters and tags, streamed chat."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app import main, queue_info, worker
from app.db import Base
from app.llm import ChunkSummary
from app.models import JobDuration, ProcessingJob, Summary, SummaryTemplate, Tag, Video, VideoChatMessage
from app.queue_info import DurationModel, fit_duration_model, queue_snapshot

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db_session(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'p3.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(main, "SessionLocal", session)
    monkeypatch.setattr(worker, "SessionLocal", session)
    from app.routes import actions as actions_routes, quality as quality_routes
    monkeypatch.setattr(actions_routes, "SessionLocal", session)
    monkeypatch.setattr(quality_routes, "SessionLocal", session)
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)
    monkeypatch.setattr(worker.settings, "data_dir", tmp_path)
    return session


def add_video(session, video_id, *, status="COMPLETED", duration=600.0, created_at=NOW, language="fr",
              transcript=None, name=None, target_language=None, job_status=None, job_kind="FULL",
              job_created_at=None, started_at=None, path=None):
    with session() as db:
        db.add(Video(
            id=video_id, filename=f"{video_id}.mp4", original_filename=name or f"{video_id}.mp4",
            path=path or f"/nowhere/{video_id}.mp4", duration_seconds=duration, size_bytes=10, status=status,
            detected_language=language, target_language=target_language, transcript_text=transcript,
            created_at=created_at,
        ))
        db.flush()
        if job_status:
            db.add(ProcessingJob(
                id=f"job-{video_id}", video_id=video_id, kind=job_kind, status=job_status, stage=job_status,
                progress=0, created_at=job_created_at or created_at, started_at=started_at, rq_job_id=f"rq-{video_id}",
            ))
        db.commit()


def add_durations(session, kind, points, translated=False):
    with session() as db:
        for index, (media, elapsed) in enumerate(points):
            db.add(JobDuration(kind=kind, media_seconds=media, elapsed_seconds=elapsed, translated=translated,
                               finished_at=NOW - timedelta(minutes=index)))
        db.commit()


# --- n°13: duration model and queue ----------------------------------------------------

def test_duration_model_needs_history_then_uses_a_ratio_then_a_line():
    assert fit_duration_model([]) is None
    assert fit_duration_model([(600, 60)]) == DurationModel(0.0, 0.1)
    # Corpus-like points: fixed cost ~20 s plus ~0.034 s per media second.
    model = fit_duration_model([(180, 26), (3060, 124), (6420, 238), (11460, 410)])
    assert 15 < model.fixed < 30
    assert 0.03 < model.per_media_second < 0.04
    assert 380 < model.predict(11460) < 440


def test_duration_model_falls_back_to_the_ratio_when_the_line_makes_no_sense():
    # Longer media finishing faster (cache, resumed jobs): a negative slope is noise.
    model = fit_duration_model([(100, 50), (200, 40), (300, 30)])
    assert model.fixed == 0.0 and model.per_media_second == pytest.approx(0.2)


def test_queue_positions_and_cumulative_estimates(db_session):
    session = db_session
    add_durations(session, "FULL", [(600, 60), (1200, 120)])  # 0.1 s per media second
    add_video(session, "run", status="PROCESSING", duration=1200, job_status="RUNNING",
              job_created_at=NOW - timedelta(minutes=5), started_at=NOW - timedelta(seconds=30))
    add_video(session, "q1", status="QUEUED", duration=600, job_status="QUEUED", job_created_at=NOW - timedelta(minutes=4))
    add_video(session, "q2", status="QUEUED", duration=3000, job_status="QUEUED", job_created_at=NOW - timedelta(minutes=3))
    add_video(session, "done", job_status="COMPLETED")

    with session() as db:
        snapshot = queue_snapshot(db, now=NOW)

    assert set(snapshot) == {"job-run", "job-q1", "job-q2"}
    assert snapshot["job-run"] == queue_info.QueueInfo(0, pytest.approx(90.0))  # 120 expected, 30 elapsed
    assert snapshot["job-q1"] == queue_info.QueueInfo(1, pytest.approx(150.0))  # 90 + 60
    assert snapshot["job-q2"] == queue_info.QueueInfo(2, pytest.approx(450.0))  # 150 + 300


def test_queue_without_history_has_positions_but_no_estimate(db_session):
    add_video(db_session, "q1", status="QUEUED", job_status="QUEUED")
    with db_session() as db:
        assert queue_snapshot(db, now=NOW) == {"job-q1": queue_info.QueueInfo(1, None)}


def test_running_job_over_its_estimate_is_almost_done(db_session):
    add_durations(db_session, "FULL", [(600, 60)])
    add_video(db_session, "run", status="PROCESSING", job_status="RUNNING", started_at=NOW - timedelta(hours=1))
    with db_session() as db:
        assert queue_snapshot(db, now=NOW)["job-run"].seconds_remaining == 0.0


def test_translated_jobs_use_their_own_history_once_there_is_enough(db_session):
    add_durations(db_session, "FULL", [(600, 60)] * 3)
    add_durations(db_session, "FULL", [(600, 300)] * 3, translated=True)
    add_video(db_session, "q1", status="QUEUED", job_status="QUEUED", target_language="anglais")
    with db_session() as db:
        assert queue_snapshot(db, now=NOW)["job-q1"].seconds_remaining == pytest.approx(300.0)


def test_completed_job_records_its_duration(db_session):
    add_video(db_session, "v", status="PROCESSING", duration=900, job_status="RUNNING",
              started_at=datetime.now(timezone.utc) - timedelta(seconds=45))
    worker.set_job("job-v", stage="COMPLETED", status="COMPLETED", progress=100)
    with db_session() as db:
        durations = db.query(JobDuration).all()
    assert len(durations) == 1
    assert durations[0].kind == "FULL" and durations[0].media_seconds == 900
    assert 44 <= durations[0].elapsed_seconds < 60


def test_job_and_video_detail_expose_the_queue_position(client, db_session):
    add_durations(db_session, "FULL", [(600, 60)])
    add_video(db_session, "q1", status="QUEUED", job_status="QUEUED")
    job = client.get("/jobs/job-q1").json()
    assert job["queue_position"] == 1
    assert job["estimated_seconds_remaining"] == pytest.approx(60.0)
    assert client.get("/videos/q1").json()["job"]["queue_position"] == 1


# --- n°14: cancellation -----------------------------------------------------------------

@pytest.fixture
def stopped(monkeypatch):
    calls = []
    monkeypatch.setattr(main, "_stop_rq_job", lambda rq_job_id, *, running: calls.append((rq_job_id, running)))
    return calls


def test_cancel_a_queued_job(client, db_session, stopped):
    add_video(db_session, "q1", status="QUEUED", job_status="QUEUED")
    response = client.post("/jobs/job-q1/cancel")
    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    with db_session() as db:
        assert db.get(Video, "q1").status == "CANCELLED"
        assert db.get(ProcessingJob, "job-q1").finished_at is not None
    assert stopped == [("rq-q1", False)]


def test_cancel_a_running_job_stops_the_work_horse(client, db_session, stopped):
    add_video(db_session, "run", status="PROCESSING", job_status="RUNNING", started_at=NOW)
    assert client.post("/jobs/job-run/cancel").status_code == 200
    assert stopped == [("rq-run", True)]
    with db_session() as db:
        assert db.get(Video, "run").status == "CANCELLED"


def test_cancelling_a_regeneration_keeps_the_video_completed(client, db_session, stopped):
    add_video(db_session, "v", status="COMPLETED", job_status="RUNNING", job_kind="SUMMARY", started_at=NOW)
    assert client.post("/jobs/job-v/cancel").json()["status"] == "CANCELLED"
    with db_session() as db:
        assert db.get(Video, "v").status == "COMPLETED"


def test_a_finished_job_cannot_be_cancelled(client, db_session, stopped):
    add_video(db_session, "v", job_status="COMPLETED")
    response = client.post("/jobs/job-v/cancel")
    assert response.status_code == 409
    assert response.json() == {"detail": "Ce traitement est déjà terminé"}
    assert client.post("/jobs/missing/cancel").status_code == 404
    assert stopped == []


def test_worker_stops_at_its_next_progress_update_once_cancelled(db_session):
    add_video(db_session, "v", status="CANCELLED", job_status="CANCELLED")
    with pytest.raises(worker.JobCancelled):
        worker.set_job("job-v", progress=40)


def test_cancelled_regeneration_saves_nothing_and_is_not_a_failure(db_session, monkeypatch):
    session = db_session
    add_video(session, "v", status="COMPLETED", transcript="[00:00:00] Bonjour", job_status="QUEUED", job_kind="SUMMARY")
    with session() as db:
        db.add(SummaryTemplate(id="tpl", name="Réunion", prompt="# Décisions", is_default=True))
        db.commit()
    monkeypatch.setattr(worker, "summarize_chunk", lambda *_, **__: ChunkSummary("- ok"))

    def final_summary_then_cancel(*_, **__):
        # The user cancels during the final LLM call, and the stop command is lost.
        with session() as db:
            db.get(ProcessingJob, "job-v").status = "CANCELLED"
            db.commit()
        return "# Résumé"

    monkeypatch.setattr(worker, "final_summary", final_summary_then_cancel)
    worker.run_summary("job-v")  # returns normally: RQ must not record a failure

    with session() as db:
        assert db.query(Summary).count() == 0
        assert db.get(ProcessingJob, "job-v").status == "CANCELLED"
        assert db.get(Video, "v").status == "COMPLETED"


def test_a_queued_video_can_be_deleted_and_a_running_one_cannot(client, db_session, stopped, tmp_path):
    source = tmp_path / "q1.mp4"
    source.write_bytes(b"media")
    add_video(db_session, "q1", status="QUEUED", job_status="QUEUED", path=str(source))
    add_video(db_session, "run", status="PROCESSING", job_status="RUNNING", started_at=NOW)

    assert client.delete("/videos/q1").status_code == 200
    assert not source.exists()
    assert stopped == [("rq-q1", False)]
    response = client.delete("/videos/run")
    assert response.status_code == 409
    assert response.json() == {"detail": "Annulez le traitement en cours avant de supprimer la vidéo"}


def test_a_cancelled_video_can_be_retried(client, db_session, monkeypatch, tmp_path):
    from tests.test_upload import SuccessfulQueue

    monkeypatch.setattr(main, "Queue", SuccessfulQueue)
    source = tmp_path / "c.mp4"
    source.write_bytes(b"media")
    add_video(db_session, "c", status="CANCELLED", job_status="CANCELLED", path=str(source))
    response = client.post("/videos/c/retry")
    assert response.status_code == 200
    assert response.json()["status"] == "QUEUED"


# --- n°17: library ----------------------------------------------------------------------

def test_library_lists_tags_and_latest_job_in_one_call(client, db_session):
    add_video(db_session, "old", created_at=NOW - timedelta(days=3), job_status="COMPLETED")
    add_video(db_session, "new", status="QUEUED", created_at=NOW, job_status="QUEUED")
    client.put("/videos/old/tags", json={"tags": ["Client A"]})

    rows = client.get("/videos").json()

    assert [row["id"] for row in rows] == ["new", "old"]
    assert rows[0]["job"]["status"] == "QUEUED" and rows[0]["job"]["queue_position"] == 1
    assert rows[1]["tags"] == ["Client A"]
    assert rows[1]["job"]["queue_position"] is None
    assert "transcript_text" not in rows[0]


def test_library_filters(client, db_session):
    add_video(db_session, "fr-done", language="fr", created_at=NOW - timedelta(days=40), transcript="[00:00:00] Budget validé")
    add_video(db_session, "en-failed", status="FAILED", language="en", created_at=NOW - timedelta(days=1),
              transcript="[00:00:00] Quarterly roadmap", name="Réunion équipe.mp4")
    add_video(db_session, "running", status="PROCESSING", language="fr", created_at=NOW)
    client.put("/videos/fr-done/tags", json={"tags": ["Finance"]})

    def ids(query):
        response = client.get(f"/videos{query}")
        assert response.status_code == 200, response.text
        return [row["id"] for row in response.json()]

    assert ids("?status=ACTIVE") == ["running"]
    assert ids("?status=FAILED") == ["en-failed"]
    assert ids("?language=en") == ["en-failed"]
    assert ids("?tag=finance") == ["fr-done"]
    assert ids("?q=budget") == ["fr-done"]
    assert ids("?q=ROADMAP%20quarterly") == ["en-failed"]
    assert ids("?q=équipe") == ["en-failed"]
    assert ids("?q=budget%20roadmap") == []
    assert ids("?q=%22%21") == ["running", "en-failed", "fr-done"]  # no word: no search
    after = (NOW - timedelta(days=7)).isoformat().replace("+00:00", "Z")
    assert set(ids(f"?created_after={after}")) == {"running", "en-failed"}
    assert client.get("/videos?status=UNKNOWN").status_code == 422


def test_search_words_drop_operators():
    assert main.search_words("  Réunion & budget:* | 'x' ") == ["réunion", "budget", "x"]
    assert main.search_words(None) == []


# --- n°17: tags -------------------------------------------------------------------------

def test_tags_are_normalised_shared_and_cleaned_up(client, db_session):
    add_video(db_session, "a")
    add_video(db_session, "b")
    response = client.put("/videos/a/tags", json={"tags": ["  Client   A ", "client a", "Projet X", ""]})
    assert response.json() == {"tags": ["Client A", "Projet X"]}
    # The existing spelling wins, whatever the case typed on another video.
    assert client.put("/videos/b/tags", json={"tags": ["CLIENT A"]}).json() == {"tags": ["Client A"]}
    assert client.get("/tags").json() == [{"name": "Client A", "count": 2}, {"name": "Projet X", "count": 1}]
    assert client.get("/videos/a").json()["tags"] == ["Client A", "Projet X"]

    client.put("/videos/a/tags", json={"tags": []})
    assert client.get("/tags").json() == [{"name": "Client A", "count": 1}]
    with db_session() as db:
        assert [tag.name for tag in db.query(Tag)] == ["Client A"]


def test_deleting_a_video_removes_its_orphan_tags(client, db_session):
    add_video(db_session, "a", job_status="COMPLETED")
    client.put("/videos/a/tags", json={"tags": ["Seul"]})
    assert client.delete("/videos/a").status_code == 200
    with db_session() as db:
        assert db.query(Tag).count() == 0


def test_tag_limits(client, db_session):
    add_video(db_session, "a")
    assert client.put("/videos/a/tags", json={"tags": ["x" * 41]}).status_code == 422
    assert client.put("/videos/a/tags", json={"tags": [f"t{i}" for i in range(21)]}).status_code == 422
    assert client.put("/videos/missing/tags", json={"tags": ["x"]}).status_code == 404


# --- n°16: streamed chat ------------------------------------------------------------------

def sse_events(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_streamed_answer_arrives_in_pieces_then_is_saved(client, db_session, monkeypatch):
    add_video(db_session, "v", transcript="[00:00:00] Le budget est validé")
    prompts = []

    async def fake_stream(prompt, **kwargs):
        prompts.append(prompt)
        for piece in ["Le budget ", "est validé ", "[00:00:00]."]:
            yield piece

    monkeypatch.setattr(main, "stream_chat", fake_stream)
    response = client.post("/videos/v/chat/stream", json={"question": "Et le budget ?"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    events = sse_events(response.text)
    assert [data["text"] for name, data in events if name == "delta"] == ["Le budget ", "est validé ", "[00:00:00]."]
    name, done = events[-1]
    assert name == "done"
    assert [(m["role"], m["content"]) for m in done["messages"]] == [
        ("user", "Et le budget ?"), ("assistant", "Le budget est validé [00:00:00]."),
    ]
    assert "Le budget est validé" in prompts[0] and "Et le budget ?" in prompts[0]
    with db_session() as db:
        assert db.query(VideoChatMessage).count() == 2


def test_streamed_answer_failure_keeps_the_partial_answer(client, db_session, monkeypatch):
    add_video(db_session, "v", transcript="[00:00:00] Bonjour")

    async def failing_stream(prompt, **kwargs):
        yield "Début"
        raise ConnectionError("Ollama stopped")

    monkeypatch.setattr(main, "stream_chat", failing_stream)
    events = sse_events(client.post("/videos/v/chat/stream", json={"question": "Q ?"}).text)
    assert events[-1] == ("error", {"detail": "Assistant indisponible, réessayez ultérieurement", "saved": True})
    with db_session() as db:
        messages = db.query(VideoChatMessage).order_by(VideoChatMessage.created_at).all()
        assert [(m.role, m.content, m.interrupted) for m in messages] == [("user", "Q ?", False), ("assistant", "Début", True)]


def test_streamed_answer_failure_without_output_saves_nothing(client, db_session, monkeypatch):
    add_video(db_session, "v", transcript="[00:00:00] Bonjour")

    async def failing_stream(prompt, **kwargs):
        raise ConnectionError("Ollama stopped")
        yield  # pragma: no cover

    monkeypatch.setattr(main, "stream_chat", failing_stream)
    events = sse_events(client.post("/videos/v/chat/stream", json={"question": "Q ?"}).text)
    assert events[-1][0] == "error" and events[-1][1]["saved"] is False
    with db_session() as db:
        assert db.query(VideoChatMessage).count() == 0


def test_streamed_chat_checks_the_video_before_streaming(client, db_session):
    add_video(db_session, "v")  # no transcript yet
    assert client.post("/videos/v/chat/stream", json={"question": "Q ?"}).status_code == 409
    assert client.post("/videos/missing/chat/stream", json={"question": "Q ?"}).status_code == 404
