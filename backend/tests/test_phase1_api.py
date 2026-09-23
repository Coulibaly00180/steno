"""API behavior added in phase 1: templates, glossary, upload options, video detail, chat language."""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import main
from app.models import GlossaryTerm, ProcessingJob, Summary, SummaryTemplate, Video


class SuccessfulQueue:
    def __init__(self, *args, **kwargs):
        pass

    def enqueue(self, *args, **kwargs):
        return SimpleNamespace(id="rq-job")


@pytest.fixture
def templates(upload_environment):
    session, _ = upload_environment
    with session() as db:
        db.add_all([
            SummaryTemplate(id="meeting", name="Compte-rendu de réunion", prompt="# A", is_default=True),
            SummaryTemplate(id="course", name="Cours / formation", prompt="# B", is_default=False),
        ])
        db.commit()
    return session


def upload(client, **data):
    return client.post("/videos", files={"file": ("clip.mp4", b"media", "video/mp4")}, data=data)


@pytest.fixture
def queue(monkeypatch):
    monkeypatch.setattr(main, "Queue", SuccessfulQueue)


# --- Templates (n°12) ---------------------------------------------------------

def test_update_template(client, templates):
    response = client.put("/templates/course", json={"name": "Cours", "description": "d", "prompt": "# Objectifs"})
    assert response.status_code == 200
    body = response.json()
    assert (body["name"], body["prompt"]) == ("Cours", "# Objectifs")
    assert body["updated_at"] is not None


def test_update_template_name_conflict_and_missing(client, templates):
    conflict = client.put("/templates/course", json={"name": "Compte-rendu de réunion", "prompt": "# B"})
    assert conflict.status_code == 409
    assert client.put("/templates/nope", json={"name": "X", "prompt": "# B"}).status_code == 404


def test_duplicate_names_copies(client, templates):
    first = client.post("/templates/course/duplicate").json()
    second = client.post("/templates/course/duplicate").json()
    assert (first["name"], second["name"]) == ("Cours / formation (copie)", "Cours / formation (copie 2)")
    assert first["prompt"] == "# B" and first["is_default"] is False


def test_duplicate_keeps_names_within_120_characters(client, upload_environment):
    session, _ = upload_environment
    with session() as db:
        db.add(SummaryTemplate(id="long", name="x" * 120, prompt="# A", is_default=False))
        db.commit()
    assert len(client.post("/templates/long/duplicate").json()["name"]) == 120


def test_set_default_moves_the_flag(client, templates):
    response = client.post("/templates/course/default")
    assert response.status_code == 200
    with templates() as db:
        assert [t.id for t in db.query(SummaryTemplate).filter_by(is_default=True)] == ["course"]


def test_default_template_cannot_be_deleted(client, templates):
    response = client.delete("/templates/meeting")
    assert response.status_code == 409
    assert "autre template par défaut" in response.json()["detail"]


def test_template_in_use_cannot_be_deleted(client, templates, upload_environment):
    session, uploads = upload_environment
    with session() as db:
        db.add(Video(id="v", filename="f", original_filename="f.mp4", path=str(uploads / "f"),
                     duration_seconds=1, size_bytes=1, status="QUEUED"))
        db.flush()
        db.add(ProcessingJob(id="j", video_id="v", status="QUEUED", stage="QUEUED", progress=0, template_id="course"))
        db.commit()
    assert client.delete("/templates/course").status_code == 409

    with session() as db:
        db.get(ProcessingJob, "j").status = "COMPLETED"
        db.commit()
    assert client.delete("/templates/course").json() == {"deleted": True}


def test_deleted_template_is_reported_on_existing_summaries(client, templates, upload_environment):
    session, uploads = upload_environment
    with session() as db:
        db.add(Video(id="v", filename="f", original_filename="f.mp4", path=str(uploads / "f"),
                     duration_seconds=60, size_bytes=1, status="COMPLETED"))
        db.flush()
        db.add(Summary(id="s", video_id="v", template_id="course", content_markdown="# B", model="m",
                       summary_length="standard", created_at=datetime.now(timezone.utc)))
        db.commit()
    assert client.get("/videos/v").json()["summaries"][0]["template_name"] == "Cours / formation"

    client.delete("/templates/course")

    summary = client.get("/videos/v").json()["summaries"][0]
    assert summary["template_id"] == "course"
    assert summary["template_name"] is None
    assert summary["summary_length"] == "standard"


# --- Glossary (n°11) ----------------------------------------------------------

def test_glossary_round_trip(client, upload_environment):
    assert client.get("/glossary").json() == {"terms": []}
    response = client.put("/glossary", json={"terms": ["OKR, Doñana", "okr", " Kubernetes "]})
    assert response.json() == {"terms": ["OKR", "Doñana", "Kubernetes"]}
    assert client.get("/glossary").json() == {"terms": ["OKR", "Doñana", "Kubernetes"]}


def test_glossary_over_limit_keeps_previous_terms(client, upload_environment):
    client.put("/glossary", json={"terms": ["OKR"]})
    response = client.put("/glossary", json={"terms": [f"t{i}" for i in range(301)]})
    assert response.status_code == 422
    assert "301 termes, limite 300" in response.json()["detail"]
    assert client.get("/glossary").json() == {"terms": ["OKR"]}


# --- Upload options (n°5, n°11, R-7) --------------------------------------------

@pytest.mark.parametrize(("data", "message"), [
    ({"summary_length": "huge"}, "Longueur de résumé invalide"),
    ({"source_language": "xx"}, "Langue source non prise en charge"),
    ({"vocabulary": "x" * 1001}, "1001 caractères, limite 1000"),
    ({"custom_prompt": "x" * 2001}, "limite 2000"),
])
def test_upload_rejects_invalid_options_without_storing(client, upload_environment, queue, data, message):
    session, uploads = upload_environment
    response = upload(client, **data)
    assert response.status_code == 422
    assert message in response.json()["detail"]
    assert list(uploads.iterdir()) == []
    with session() as db:
        assert db.query(Video).count() == 0


def test_upload_stores_options_and_snapshots_the_glossary(client, upload_environment, queue):
    session, _ = upload_environment
    client.put("/glossary", json={"terms": ["OKR", "Doñana"]})

    response = upload(client, summary_length="detailed", source_language="EN", vocabulary="Okr, Alpha")
    assert response.status_code == 200
    video_id = response.json()["video_id"]
    assert response.json()["summary_length"] == "detailed"

    client.put("/glossary", json={"terms": []})  # later edits never change this video

    with session() as db:
        video = db.get(Video, video_id)
        assert (video.detected_language, video.source_language_forced) == ("en", True)
        assert video.vocabulary == "Okr\nAlpha"
        assert video.glossary_snapshot == "OKR\nDoñana"
    detail = client.get(f"/videos/{video_id}").json()
    assert detail["vocabulary"] == ["Okr", "Alpha"]
    assert detail["glossary_snapshot"] == ["OKR", "Doñana"]
    assert detail["whisper_terms_count"] == 3  # "OKR" is a duplicate of "Okr"
    assert detail["source_language_forced"] is True


def test_upload_without_glossary(client, upload_environment, queue):
    session, _ = upload_environment
    client.put("/glossary", json={"terms": ["OKR"]})
    video_id = upload(client, use_global_glossary="false").json()["video_id"]
    with session() as db:
        video = db.get(Video, video_id)
        assert video.glossary_snapshot is None
        assert video.source_language_forced is False
        assert db.query(ProcessingJob).one().summary_length == "standard"


def test_upload_fails_loudly_when_the_glossary_is_unreadable(client, upload_environment, queue, monkeypatch):
    def broken():
        raise RuntimeError("database down")
    monkeypatch.setattr(main, "_glossary_terms", broken)
    response = upload(client)
    assert response.status_code == 503


def test_retry_keeps_the_summary_length(client, upload_environment, queue):
    session, uploads = upload_environment
    source = uploads / "s.mp4"
    source.write_bytes(b"media")
    with session() as db:
        db.add(Video(id="v", filename="s.mp4", original_filename="s.mp4", path=str(source),
                     duration_seconds=1, size_bytes=5, status="FAILED"))
        db.flush()
        db.add(ProcessingJob(id="old", video_id="v", status="FAILED", stage="FAILED", progress=0,
                             summary_length="short", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc)))
        db.commit()
    assert client.post("/videos/v/retry").json()["summary_length"] == "short"


# --- Chat language (n°7) --------------------------------------------------------

def test_chat_passes_fallback_language_and_vocabulary(client, upload_environment, monkeypatch):
    session, uploads = upload_environment
    with session() as db:
        db.add(Video(id="v", filename="f", original_filename="f.mp4", path=str(uploads / "f"),
                     duration_seconds=1, size_bytes=1, status="COMPLETED", transcript_text="[00:00:00] Bonjour",
                     detected_language="fr", vocabulary="Okr", glossary_snapshot="OKR\nDoñana"))
        db.commit()
    calls = []

    def fake_answer(question, context, history, **kwargs):
        calls.append(kwargs)
        return "answer"

    monkeypatch.setattr(main, "answer_video_question", fake_answer)

    client.post("/videos/v/chat/messages", json={"question": "ok ?"})
    client.post("/videos/v/chat/messages", json={"question": "What were the decisions?"})
    client.post("/videos/v/chat/messages", json={"question": "ok ?"})

    assert calls[0]["fallback_language"] == "français"
    assert "What were the decisions?" in calls[2]["fallback_language"]
    assert calls[0]["vocabulary"] == ["Okr", "Doñana"]


def test_glossary_model_rejects_case_duplicates_at_database_level(upload_environment):
    session, _ = upload_environment
    import sqlalchemy.exc
    with session() as db:
        db.add_all([GlossaryTerm(term="OKR", position=0), GlossaryTerm(term="okr", position=1)])
        with pytest.raises(sqlalchemy.exc.IntegrityError):
            db.commit()
