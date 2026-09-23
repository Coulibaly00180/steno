"""Phase 4: passages and embeddings, chat on a whole video, questions on several videos."""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app import main, retrieval, worker
from app.db import Base
from app.models import Passage, ProcessingJob, TranscriptSegment, Video, VideoIndex
from app.queue_info import queue_snapshot
from app.retrieval import Hit, build_passages, merge_hits, retrieval_query, search, transcript_hash
from tests.conftest import fake_embedding

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
# A long talk: the 3rd hour speaks about fault injection on a Bluetooth tracker.
TOPICS = [
    (0, "radio encryption used by armies and the key schedule of the cipher"),
    (3600, "fuzzing web applications written in PHP and the plugins found vulnerable"),
    (7300, "fault injection on the Bluetooth tracker chip to dump the firmware and get remote code execution"),
    (9200, "semiconductor supply chain, lithography machines and geopolitics of chips"),
]


@pytest.fixture
def db_session(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'p4.db'}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(main, "SessionLocal", session)
    monkeypatch.setattr(worker, "SessionLocal", session)
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)
    monkeypatch.setattr(worker.settings, "data_dir", tmp_path)
    monkeypatch.setattr(main.settings, "embedding_model", "test-embed")
    return session


def talk_segments():
    """3 h 20 of 10-second segments, each topic repeated for its hour."""
    rows = []
    for index, (start, topic) in enumerate(TOPICS):
        end = TOPICS[index + 1][0] if index + 1 < len(TOPICS) else 12000
        for second in range(start, end, 10):
            rows.append((float(second), float(second + 10), f"Now about {topic}, part {second}."))
    return rows


def add_video(session, video_id, segments, *, status="COMPLETED", name=None, created_at=NOW):
    from app.utils import timestamp

    transcript = "\n".join(f"[{timestamp(start)}] {text}" for start, _, text in segments)
    with session() as db:
        db.add(Video(
            id=video_id, filename=f"{video_id}.mp4", original_filename=name or f"{video_id}.mp4",
            path=f"/nowhere/{video_id}.mp4", duration_seconds=segments[-1][1] if segments else 60, size_bytes=1,
            status=status, detected_language="en", transcript_text=transcript or None, created_at=created_at,
        ))
        db.flush()
        db.add_all([TranscriptSegment(video_id=video_id, start_seconds=s, end_seconds=e, text=t) for s, e, t in segments])
        db.commit()


# --- passages ---------------------------------------------------------------------------------

def test_passages_follow_segments_with_one_segment_overlap():
    segments = [(float(i * 10), float(i * 10 + 10), f"Phrase numéro {i} " + "mot " * 30) for i in range(20)]
    passages = build_passages(segments)
    assert all(len(p.plain) <= retrieval.PASSAGE_TARGET_CHARS + 200 for p in passages)
    assert passages[0].start_seconds == 0.0
    assert passages[-1].end_seconds == 200.0
    # The last segment of a passage opens the next one.
    for previous, following in zip(passages, passages[1:]):
        assert previous.text.splitlines()[-1] == following.text.splitlines()[0]
    assert passages[0].text.startswith("[00:00:00] Phrase numéro 0")
    assert "[00:" not in passages[0].plain


def test_passages_never_span_more_than_two_minutes_and_skip_empty_text():
    segments = [(float(i * 50), float(i * 50 + 50), "court") for i in range(6)] + [(400.0, 401.0, "  ")]
    passages = build_passages(segments)
    assert all(p.end_seconds - p.start_seconds <= retrieval.PASSAGE_MAX_SECONDS for p in passages)
    assert build_passages([]) == []
    assert len(build_passages([(0.0, 5.0, "seul")])) == 1


def test_follow_up_questions_are_searched_with_the_previous_one():
    assert retrieval_query("Et ensuite ?", ["Qui a gagné le match ?"]) == "Qui a gagné le match ?\nEt ensuite ?"
    assert retrieval_query("Quel est le budget prévu pour l'année prochaine ?", ["x"]) == "Quel est le budget prévu pour l'année prochaine ?"
    assert retrieval_query("Et ensuite ?", []) == "Et ensuite ?"


def test_overlapping_hits_of_a_video_are_merged_in_order():
    hits = [
        Hit("v", 60, 120, "[00:01:00] b\n[00:01:50] c", 0.2),
        Hit("v", 0, 70, "[00:00:00] a\n[00:01:00] b", 0.1),
        Hit("w", 0, 60, "[00:00:00] z", 0.3),
    ]
    merged = merge_hits(hits)
    assert [(h.video_id, h.start_seconds, h.end_seconds) for h in merged] == [("v", 0, 120), ("w", 0, 60)]
    assert merged[0].text == "[00:00:00] a\n[00:01:00] b\n[00:01:50] c"
    assert merged[0].distance == 0.1


# --- indexing ------------------------------------------------------------------------------------

def test_index_video_stores_passages_and_marks_it_ready(db_session):
    add_video(db_session, "talk", talk_segments())
    count = worker.index_video("talk")
    with db_session() as db:
        state = db.get(VideoIndex, "talk")
        assert state.status == "READY" and state.model == "test-embed" and state.passages == count > 100
        assert state.transcript_hash == transcript_hash(db.get(Video, "talk").transcript_text)
        assert db.query(Passage).filter_by(video_id="talk").count() == count


def test_a_correction_during_indexing_is_not_overwritten(db_session, monkeypatch):
    add_video(db_session, "v", [(0.0, 5.0, "Bonjour"), (5.0, 9.0, "Au revoir")])

    def embed_then_edit(texts, on_batch=None):
        with db_session() as db:
            db.get(Video, "v").transcript_text = "[00:00:00] Bonjour corrigé"
            db.commit()
        return [fake_embedding(text) for text in texts]

    monkeypatch.setattr(worker, "embed_texts", embed_then_edit)
    assert worker.index_video("v") == 0
    with db_session() as db:
        assert db.get(VideoIndex, "v") is None


def test_pipeline_indexing_failure_leaves_the_video_usable(db_session, monkeypatch):
    add_video(db_session, "v", [(0.0, 5.0, "Bonjour")], status="PROCESSING")
    with db_session() as db:
        db.add(ProcessingJob(id="job", video_id="v", status="RUNNING", stage="GENERATING_EXPORTS", progress=96))
        db.commit()
    monkeypatch.setattr(worker, "embed_texts", lambda *_, **__: (_ for _ in ()).throw(ConnectionError("model missing")))
    worker._index_after_pipeline("job", "v")
    with db_session() as db:
        state = db.get(VideoIndex, "v")
        assert state.status == "FAILED" and "model missing" in state.error
        assert db.get(ProcessingJob, "job").status == "RUNNING"


def test_index_job_completes_and_failure_only_fails_the_job(db_session, monkeypatch):
    add_video(db_session, "v", [(0.0, 5.0, "Bonjour")])
    with db_session() as db:
        db.add_all([
            ProcessingJob(id="ok", video_id="v", kind="INDEX", status="QUEUED", stage="QUEUED", progress=0),
            ProcessingJob(id="ko", video_id="v", kind="INDEX", status="QUEUED", stage="QUEUED", progress=0),
        ])
        db.commit()
    worker.run_index("ok")
    monkeypatch.setattr(worker, "embed_texts", lambda *_, **__: (_ for _ in ()).throw(ConnectionError("down")))
    worker.run_index("ko")
    with db_session() as db:
        assert db.get(ProcessingJob, "ok").status == "COMPLETED"
        assert db.get(ProcessingJob, "ko").status == "FAILED"
        assert db.get(ProcessingJob, "ko").error == worker.ERROR_INDEX_FAILED
        assert db.get(Video, "v").status == "COMPLETED"
        assert db.get(VideoIndex, "v").status == "FAILED"


class FakeQueue:
    enqueued = []

    def __init__(self, name, **kwargs):
        self.name = name

    def enqueue(self, function, job_id, **kwargs):
        FakeQueue.enqueued.append((self.name, function, job_id))
        return SimpleNamespace(id=f"rq-{job_id}")


def test_catch_up_indexes_only_what_is_missing(db_session, monkeypatch):
    FakeQueue.enqueued = []
    monkeypatch.setattr(worker, "Queue", FakeQueue)
    monkeypatch.setattr(worker.Redis, "from_url", lambda *_: None)
    monkeypatch.setattr(worker.settings, "embedding_model", "test-embed")
    for video_id in ("missing", "ready", "other-model", "failed-video"):
        add_video(db_session, video_id, [(0.0, 5.0, "Bonjour")])
    add_video(db_session, "no-transcript", [], status="COMPLETED")
    add_video(db_session, "processing", [(0.0, 5.0, "x")], status="PROCESSING")
    with db_session() as db:
        db.add_all([
            VideoIndex(video_id="ready", status="READY", model="test-embed", transcript_hash="h", passages=1, updated_at=NOW),
            VideoIndex(video_id="other-model", status="READY", model="old-embed", transcript_hash="h", passages=1, updated_at=NOW),
        ])
        db.commit()

    assert worker.enqueue_missing_indexes() == 3
    assert worker.enqueue_missing_indexes() == 0  # already queued
    assert {name for name, _, _ in FakeQueue.enqueued} == {"video-ai-index"}
    with db_session() as db:
        queued = {job.video_id for job in db.query(ProcessingJob).filter_by(kind="INDEX")}
    assert queued == {"missing", "other-model", "failed-video"}


def test_index_jobs_wait_behind_every_analysis(db_session):
    add_video(db_session, "a", [(0.0, 5.0, "x")])
    add_video(db_session, "b", [(0.0, 5.0, "y")], status="QUEUED")
    with db_session() as db:
        db.add(ProcessingJob(id="index", video_id="a", kind="INDEX", status="QUEUED", stage="QUEUED", progress=0,
                             created_at=NOW - timedelta(minutes=5)))
        db.add(ProcessingJob(id="full", video_id="b", status="QUEUED", stage="QUEUED", progress=0, created_at=NOW))
        db.commit()
        snapshot = queue_snapshot(db, now=NOW)
    assert snapshot["full"].position == 1
    assert snapshot["index"].position == 2


# --- API: index jobs stay in the background ---------------------------------------------------------

def test_index_jobs_are_not_the_videos_job(client, db_session):
    add_video(db_session, "v", [(0.0, 5.0, "Bonjour")])
    with db_session() as db:
        db.add(ProcessingJob(id="full", video_id="v", status="COMPLETED", stage="COMPLETED", progress=100, created_at=NOW))
        db.add(ProcessingJob(id="index", video_id="v", kind="INDEX", status="RUNNING", stage="INDEXING", progress=10,
                             created_at=NOW + timedelta(minutes=1), rq_job_id="rq-index"))
        db.commit()
    assert client.get("/videos/v").json()["job"]["id"] == "full"
    assert client.get("/videos").json()[0]["job"]["id"] == "full"
    # A background index never blocks a correction.
    assert client.patch("/videos/v/segments/1", json={"text": "Bonsoir"}).status_code == 200


def test_correction_marks_the_index_stale_and_queues_a_new_one(client, db_session, monkeypatch):
    queued = []
    monkeypatch.setattr(main, "enqueue_index_job", queued.append)
    add_video(db_session, "v", [(0.0, 5.0, "Bonjour")])
    worker.index_video("v")
    assert client.patch("/videos/v/segments/1", json={"text": "Bonsoir"}).status_code == 200
    with db_session() as db:
        assert db.get(VideoIndex, "v").status == "STALE"
    assert queued == ["v"]


def test_deleting_a_video_stops_its_running_index(client, db_session, monkeypatch):
    stopped = []
    monkeypatch.setattr(main, "_stop_rq_job", lambda rq_job_id, *, running: stopped.append((rq_job_id, running)))
    add_video(db_session, "v", [(0.0, 5.0, "Bonjour")])
    with db_session() as db:
        db.add(ProcessingJob(id="index", video_id="v", kind="INDEX", status="RUNNING", stage="INDEXING", progress=10, rq_job_id="rq-index"))
        db.commit()
    assert client.delete("/videos/v").status_code == 200
    assert ("rq-index", True) in stopped


# --- n°6: chat on a whole video --------------------------------------------------------------------

def test_chat_mode(client, db_session):
    add_video(db_session, "short", [(0.0, 5.0, "Bonjour")])
    add_video(db_session, "long", talk_segments())
    assert client.get("/videos/short").json()["chat_mode"] == "full"
    assert client.get("/videos/long").json()["chat_mode"] == "partial"
    worker.index_video("long")
    assert client.get("/videos/long").json()["chat_mode"] == "passages"


def test_a_question_on_the_third_hour_reads_the_third_hour(client, db_session, monkeypatch):
    add_video(db_session, "talk", talk_segments())
    worker.index_video("talk")
    contexts = []
    monkeypatch.setattr(main, "answer_video_question", lambda question, context, history, **_: contexts.append(context) or "Réponse")

    response = client.post("/videos/talk/chat/messages", json={"question": "What about the fault injection on the Bluetooth tracker firmware?"})

    assert response.status_code == 200
    context = contexts[0]
    assert "EXTRAITS DE LA TRANSCRIPTION" in context and "tronquée" not in context
    assert "fault injection" in context and "[02:" in context
    assert "radio encryption" not in context


def test_without_index_a_long_video_still_answers_from_its_start(client, db_session, monkeypatch):
    add_video(db_session, "talk", talk_segments())
    contexts = []
    monkeypatch.setattr(main, "answer_video_question", lambda question, context, history, **_: contexts.append(context) or "Réponse")
    assert client.post("/videos/talk/chat/messages", json={"question": "Fault injection?"}).status_code == 200
    assert "Transcription tronquée" in contexts[0]


# --- n°19: questions on several videos -----------------------------------------------------------------

def sse_events(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_library_question_cites_numbered_passages_from_several_videos(client, db_session, monkeypatch):
    add_video(db_session, "budget", [(float(i * 10), float(i * 10 + 10), f"Le budget annuel de l'association, point {i}.") for i in range(12)], name="Réunion budget.mp4")
    add_video(db_session, "velo", [(0.0, 10.0, "Le conseil achète des vélos électriques."), (10.0, 20.0, "Le budget vélo est voté.")], name="Conseil.mp4")
    add_video(db_session, "pending", [(0.0, 10.0, "Le budget pas encore indexé.")])
    worker.index_video("budget")
    worker.index_video("velo")
    prompts = []

    async def fake_stream(prompt, **kwargs):
        prompts.append(prompt)
        yield "Le budget est voté [1][2]."

    monkeypatch.setattr(main, "stream_chat", fake_stream)
    response = client.post("/library/chat/stream", json={
        "question": "Que dit-on du budget ?", "video_ids": ["budget", "velo", "pending", "unknown"],
        "history": [{"role": "user", "content": "Bonjour"}, {"role": "assistant", "content": "Bonjour !"}],
    })

    assert response.status_code == 200, response.text
    events = sse_events(response.text)
    name, sources = events[0]
    assert name == "sources"
    assert sources["searched"] == 2 and sources["skipped"] == 1
    videos = {source["video_id"] for source in sources["sources"]}
    assert videos == {"budget", "velo"}
    # At most LIBRARY_PASSAGES_PER_VIDEO passages of one video, before merging.
    assert sum(1 for s in sources["sources"] if s["video_id"] == "budget") <= main.LIBRARY_PASSAGES_PER_VIDEO
    assert [s["n"] for s in sources["sources"]] == list(range(1, len(sources["sources"]) + 1))
    assert events[-1] == ("done", {"answer": "Le budget est voté [1][2]."})
    assert "[1] Vidéo « " in prompts[0] and "Utilisateur : Bonjour" in prompts[0]


def test_library_question_needs_an_indexed_video(client, db_session):
    add_video(db_session, "pending", [(0.0, 10.0, "Pas encore indexé.")])
    response = client.post("/library/chat/stream", json={"question": "Q ?", "video_ids": ["pending"]})
    assert response.status_code == 409
    assert client.post("/library/chat/stream", json={"question": "Q ?", "video_ids": []}).status_code == 422


def test_search_caps_passages_per_video(db_session):
    add_video(db_session, "a", [(float(i * 10), float(i * 10 + 10), f"budget point {i}") for i in range(40)])
    add_video(db_session, "b", [(0.0, 10.0, "budget unique")])
    worker.index_video("a")
    worker.index_video("b")
    with db_session() as db:
        hits = search(db, ["a", "b"], fake_embedding("budget"), limit=6, per_video=2)
    assert len([hit for hit in hits if hit.video_id == "a"]) <= 2
    assert any(hit.video_id == "b" for hit in hits)
