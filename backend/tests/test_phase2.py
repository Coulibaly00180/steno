"""Phase 2: chapters, summary regeneration, transcript and summary editing, media."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import main, worker
from app.db import Base
from app.llm import ChunkSummary, parse_chunk_summary
from app.models import Chapter, ProcessingJob, Summary, SummaryTemplate, TranscriptSegment, Video

T0 = datetime(2026, 9, 1, tzinfo=timezone.utc)


# --- Parsing and chapter rules ------------------------------------------------------

def test_parse_chunk_summary_with_both_sections():
    parsed = parse_chunk_summary(
        "CHAPITRES\n[00:01:05] Lancement du projet\n00:12:00 - Budget\nRÉSUMÉ\n- On lance [00:01:05]\n- Budget validé [00:12:30]"
    )
    assert parsed.chapters == [("00:01:05", "Lancement du projet"), ("00:12:00", "Budget")]
    assert parsed.text == "- On lance [00:01:05]\n- Budget validé [00:12:30]"


def test_parse_chunk_summary_without_headings_keeps_bullets():
    parsed = parse_chunk_summary("[00:00:10] Introduction\n- Point A [00:00:12]\n- Point B")
    assert parsed.chapters == [("00:00:10", "Introduction")]
    assert parsed.text == "- Point A [00:00:12]\n- Point B"


@pytest.mark.parametrize(("value", "seconds"), [("01:02:03", 3723.0), ("12:30", 750.0), ("1:75", None), ("x:10", None)])
def test_parse_clock(value, seconds):
    assert worker.parse_clock(value) == seconds


def test_chapters_outside_the_block_are_dropped_and_close_ones_merged():
    block_range = worker.chunk_time_range("[00:10:00] a\n[00:20:00] b")
    assert block_range == (600.0, 1200.0)
    kept = worker.valid_chapters([("00:10:00", "Début"), ("00:59:00", "Inventé"), ("00:15:00", "Suite")], block_range)
    assert kept == [(600.0, "Début"), (900.0, "Suite")]
    assert worker.merge_chapters([(900.0, "Suite"), (600.0, "Début"), (605.0, "Doublon"), (1000.0, "suite")]) == [
        (600.0, "Début"), (900.0, "Suite"),
    ]
    # Short video: topics 13 s and 23 s apart are real chapters.
    assert worker.merge_chapters([(0.0, "Intro"), (13.0, "Bêta"), (36.0, "Budget"), (59.0, "Recrutement")]) == [
        (0.0, "Intro"), (13.0, "Bêta"), (36.0, "Budget"), (59.0, "Recrutement"),
    ]


# --- Worker ---------------------------------------------------------------------------

@pytest.fixture
def env(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'p2.db'}")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(worker, "SessionLocal", session)
    monkeypatch.setattr(main, "SessionLocal", session)
    monkeypatch.setattr(worker.settings, "data_dir", tmp_path)
    (tmp_path / "uploads").mkdir()
    source = tmp_path / "uploads" / "v.mp4"
    source.write_bytes(b"0123456789" * 100)
    with session() as db:
        db.add(SummaryTemplate(id="tpl", name="Réunion", prompt="# Décisions", is_default=True))
        db.add(Video(
            id="v", filename="v.mp4", original_filename="v.mp4", path=str(source), duration_seconds=1200,
            size_bytes=1000, status="COMPLETED", detected_language="fr",
            transcript_text="[00:00:00] Bonjour Ava\n[00:10:00] Budget validé",
        ))
        db.flush()
        db.add_all([
            TranscriptSegment(video_id="v", start_seconds=0, end_seconds=5, text="Bonjour Ava"),
            TranscriptSegment(video_id="v", start_seconds=600, end_seconds=605, text="Budget validé, merci ava."),
        ])
        db.add(ProcessingJob(id="full", video_id="v", status="COMPLETED", stage="COMPLETED", progress=100, created_at=T0))
        db.add(Summary(id="s1", video_id="v", template_id="tpl", content_markdown="# Ancien", model="m",
                       summary_length="standard", created_at=T0))
        db.commit()
    return session, tmp_path


def add_summary_job(session, job_id="sum", **fields):
    with session() as db:
        db.add(ProcessingJob(id=job_id, video_id="v", kind="SUMMARY", status="QUEUED", stage="QUEUED", progress=0,
                             created_at=datetime.now(timezone.utc), **fields))
        db.commit()


def fake_llm(monkeypatch, calls):
    def chunk(text, language, **kwargs):
        calls.append(("chunk", text))
        return ChunkSummary("- Budget [00:10:00]", [("00:00:00", "Accueil"), ("00:10:00", "Budget"), ("02:00:00", "Hors bloc")])
    monkeypatch.setattr(worker, "summarize_chunk", chunk)
    monkeypatch.setattr(worker, "final_summary", lambda intermediate, template, language, **kw: calls.append(("final", kw)) or "# Nouveau [00:10:00]")
    monkeypatch.setattr(worker, "translate_chunk", lambda text, language, **_: calls.append(("translate", text)) or f"EN {text}")


def test_regeneration_keeps_the_video_completed_and_writes_chapters(env, monkeypatch):
    session, data = env
    calls = []
    fake_llm(monkeypatch, calls)
    add_summary_job(session, template_id="tpl", summary_length="short", custom_prompt="Tutoie.")

    worker.run_summary("sum")

    with session() as db:
        assert db.get(Video, "v").status == "COMPLETED"
        assert db.get(ProcessingJob, "sum").status == "COMPLETED"
        summaries = db.query(Summary).order_by(Summary.created_at).all()
        assert [s.content_markdown for s in summaries] == ["# Ancien", "# Nouveau [00:10:00]"]
        assert summaries[-1].summary_length == "short"
        assert [(c.start_seconds, c.title) for c in db.query(Chapter).order_by(Chapter.start_seconds)] == [(0.0, "Accueil"), (600.0, "Budget")]
    final_kwargs = [call[1] for call in calls if call[0] == "final"][0]
    assert final_kwargs["instructions"] == "Tutoie."
    exports = data / "exports" / "v"
    assert (exports / "summary.md").read_text(encoding="utf-8") == "# Nouveau [00:10:00]"
    assert (exports / "chapters.txt").read_text(encoding="utf-8") == "00:00:00 Accueil\n00:10:00 Budget\n"
    assert json.loads((exports / "metadata.json").read_text(encoding="utf-8"))["summary_length"] == "short"


def test_regeneration_reuses_block_summaries_for_the_same_text(env, monkeypatch):
    session, _ = env
    calls = []
    fake_llm(monkeypatch, calls)
    add_summary_job(session, job_id="a", summary_length="standard")
    worker.run_summary("a")
    add_summary_job(session, job_id="b", summary_length="short", template_id="tpl")  # same detail level
    worker.run_summary("b")
    add_summary_job(session, job_id="c", summary_length="detailed")  # other detail level: recomputed
    worker.run_summary("c")

    assert [kind for kind, _ in calls].count("chunk") == 2
    assert [kind for kind, _ in calls].count("final") == 3


def test_failed_regeneration_keeps_previous_summary(env, monkeypatch):
    session, _ = env
    calls = []
    fake_llm(monkeypatch, calls)
    monkeypatch.setattr(worker, "final_summary", lambda *a, **k: (_ for _ in ()).throw(ConnectionError("ollama down")))
    add_summary_job(session)

    with pytest.raises(ConnectionError):
        worker.run_summary("sum")

    with session() as db:
        job = db.get(ProcessingJob, "sum")
        assert (job.status, job.error) == ("FAILED", worker.ERROR_SUMMARY_FAILED)
        assert db.get(Video, "v").status == "COMPLETED"
        assert db.query(Summary).count() == 1


def test_stale_translation_is_redone_fresh_one_reused(env, monkeypatch):
    session, _ = env
    calls = []
    fake_llm(monkeypatch, calls)
    with session() as db:
        video = db.get(Video, "v")
        video.target_language = "anglais"
        video.translated_text = "Hello"
        video.translated_at = T0
        db.commit()
    add_summary_job(session, job_id="fresh")
    worker.run_summary("fresh")
    assert not [c for c in calls if c[0] == "translate"]

    with session() as db:
        db.get(Video, "v").transcript_edited_at = T0 + timedelta(days=1)
        db.commit()
    add_summary_job(session, job_id="stale")
    worker.run_summary("stale")
    assert [c for c in calls if c[0] == "translate"]
    with session() as db:
        assert db.get(Video, "v").translated_text.startswith("EN ")


# --- API --------------------------------------------------------------------------------

class Queue:
    enqueued = []

    def __init__(self, *args, **kwargs):
        pass

    def enqueue(self, name, job_id, **kwargs):
        type(self).enqueued.append((name, job_id))
        return SimpleNamespace(id="rq")


def test_regenerate_endpoint_enqueues_a_summary_job(client, env, monkeypatch):
    session, _ = env
    Queue.enqueued = []
    monkeypatch.setattr(main, "Queue", Queue)

    response = client.post("/videos/v/summaries", json={"template_id": "tpl", "summary_length": "detailed", "custom_prompt": "Court."})

    assert response.status_code == 200
    body = response.json()
    assert (body["kind"], body["summary_length"]) == ("SUMMARY", "detailed")
    assert Queue.enqueued == [("app.worker.run_summary", body["id"])]
    # A second request while the first is queued is refused.
    assert client.post("/videos/v/summaries", json={}).status_code == 409


@pytest.mark.parametrize(("payload", "status"), [
    ({"summary_length": "huge"}, 422),
    ({"template_id": "nope"}, 404),
    ({"custom_prompt": "x" * 2001}, 422),
])
def test_regenerate_validation(client, env, monkeypatch, payload, status):
    monkeypatch.setattr(main, "Queue", Queue)
    assert client.post("/videos/v/summaries", json=payload).status_code == status


def test_regenerate_requires_a_completed_video(client, env, monkeypatch):
    session, _ = env
    monkeypatch.setattr(main, "Queue", Queue)
    with session() as db:
        db.get(Video, "v").status = "FAILED"
        db.commit()
    assert client.post("/videos/v/summaries", json={}).status_code == 409


def test_segment_edit_rebuilds_transcript_and_marks_summary_outdated(client, env):
    session, data = env
    segment_id = client.get("/videos/v").json()["segments"][0]["id"]
    assert client.get("/videos/v").json()["summary_outdated"] is False

    response = client.patch(f"/videos/v/segments/{segment_id}", json={"text": "  Bonjour Awa  "})

    assert response.json()["text"] == "Bonjour Awa"
    detail = client.get("/videos/v").json()
    assert detail["transcript_text"].startswith("[00:00:00] Bonjour Awa\n[00:10:00]")
    assert detail["summary_outdated"] is True
    assert (data / "exports" / "v" / "transcript.txt").read_text(encoding="utf-8").startswith("[00:00:00] Bonjour Awa")
    assert client.patch(f"/videos/v/segments/{segment_id}", json={"text": "   "}).status_code == 422


def test_replace_is_literal_and_respects_options(client, env):
    session, _ = env
    whole = client.post("/videos/v/transcript/replace", json={"find": "ava", "replace": "Awa", "whole_word": True})
    assert whole.json() == {"replaced": 2, "segments": 2}
    texts = [s["text"] for s in client.get("/videos/v").json()["segments"]]
    assert texts == ["Bonjour Awa", "Budget validé, merci Awa."]

    case = client.post("/videos/v/transcript/replace", json={"find": "awa", "replace": r"\g<0>X", "match_case": True})
    assert case.json()["replaced"] == 0
    literal = client.post("/videos/v/transcript/replace", json={"find": "Awa", "replace": r"\1"})
    assert literal.json()["replaced"] == 2
    assert client.get("/videos/v").json()["segments"][0]["text"] == r"Bonjour \1"


def test_edits_are_refused_while_a_job_runs(client, env):
    session, _ = env
    add_summary_job(session)
    segment_id = client.get("/videos/v").json()["segments"][0]["id"]
    assert client.patch(f"/videos/v/segments/{segment_id}", json={"text": "x"}).status_code == 409
    assert client.put("/videos/v/summaries/s1", json={"content_markdown": "x"}).status_code == 409
    assert client.post("/videos/v/transcript/replace", json={"find": "a"}).status_code == 409


def test_summary_edit_updates_exports(client, env):
    session, data = env
    response = client.put("/videos/v/summaries/s1", json={"content_markdown": "# Corrigé"})
    assert response.status_code == 200
    assert response.json()["edited_at"] is not None
    assert (data / "exports" / "v" / "summary.md").read_text(encoding="utf-8") == "# Corrigé"
    assert client.put("/videos/v/summaries/nope", json={"content_markdown": "x"}).status_code == 404


def test_media_supports_range_requests_and_audio_fallback(client, env):
    session, data = env
    full = client.get("/videos/v/media")
    assert full.status_code == 200 and full.headers["content-type"] == "video/mp4"
    part = client.get("/videos/v/media", headers={"Range": "bytes=10-19"})
    assert part.status_code == 206
    assert part.content == b"0123456789"

    assert client.get("/videos/v/audio").status_code == 404
    (data / "audio").mkdir(exist_ok=True)
    (data / "audio" / "v.wav").write_bytes(b"RIFF")
    assert client.get("/videos/v/audio").headers["content-type"] in {"audio/x-wav", "audio/wav"}
    detail = client.get("/videos/v").json()
    assert (detail["media_kind"], detail["source_available"], detail["audio_available"]) == ("video", True, True)


def test_chapters_export_is_downloadable(client, env):
    session, data = env
    with session() as db:
        db.add(Chapter(video_id="v", start_seconds=12, title="Intro"))
        db.commit()
        worker.write_exports(db, "v")
    response = client.get("/videos/v/exports/chapters.txt")
    assert response.text == "00:00:00 Intro\n"
    assert Path(data / "exports" / "v" / "metadata.json").exists()


def test_extra_chapters_are_merged_into_the_previous_section():
    chapters = [(0.0, "Intro"), (60.0, "Présentation A"), (75.0, "Analyse A"), (200.0, "Présentation B"), (215.0, "Analyse B")]
    # Each "Analyse" starts 15 s after its "Présentation": it is the same subject and merges into it.
    assert worker.keep_main_chapters(chapters, limit=3) == [(0.0, "Intro"), (60.0, "Présentation A"), (200.0, "Présentation B")]
    assert worker.keep_main_chapters(chapters, limit=10) == chapters


@pytest.mark.parametrize(("minutes", "limit"), [
    (0.5, 2), (3, 3), (11, 10), (30, 10), (51, 10), (60, 12), (90, 18), (180, 30), (360, 30),
])
def test_chapter_limit_grows_with_duration(minutes, limit):
    assert worker.chapter_limit(minutes * 60) == limit


def test_block_chapters_only_validates():
    text = "\n".join(f"[{worker.timestamp(s)}] phrase" for s in range(0, 600, 30))
    raw = [(worker.timestamp(s), f"Sujet {s}") for s in (0, 60, 90, 300, 330, 560)] + [("02:00:00", "Inventé")]
    # The text spans 0-570 s: only the invented 02:00:00 is dropped, nothing is capped here.
    assert [start for start, _ in worker.block_chapters(raw, text)] == [0, 60, 90, 300, 330, 560]
