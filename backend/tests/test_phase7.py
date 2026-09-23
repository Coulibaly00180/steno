"""Learning glossary (n°2), backups and library archive (n°13), disk space (n°14), watched folder (n°9)."""
import io
import json
import os
import subprocess
import tarfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app import app_settings, backups, learning, main, portable, storage, watch_folder, worker
from app.db import Base
from app.models import (
    GlossaryTerm,
    LibraryConversation,
    LibraryMessage,
    ProcessingJob,
    Speaker,
    Summary,
    SummaryTemplate,
    TermCorrection,
    TranscriptSegment,
    Video,
)
from app.schemas import BackupSettings, WatchFolderSettings
from tests.test_upload import FailingQueue, SuccessfulQueue

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=timezone.utc)
MODULES = (main, worker, storage, backups, watch_folder, portable)


def make_session(path: Path):
    engine = create_engine(f"sqlite:///{path}")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def env(monkeypatch, tmp_path):
    session = make_session(tmp_path / "p7.db")
    for module in MODULES:
        monkeypatch.setattr(module, "SessionLocal", session)
    monkeypatch.setattr(main.settings, "data_dir", tmp_path / "data")
    for folder in ("uploads", "audio", "exports", "inbox", "backups"):
        (tmp_path / "data" / folder).mkdir(parents=True)
    monkeypatch.setattr(main, "Queue", SuccessfulQueue)
    monkeypatch.setattr(main, "ffprobe_duration", lambda _, **__: 12.0)
    monkeypatch.setattr(worker, "enqueue_index_job", lambda video_id: None)
    return session


def add_video(session, video_id="v", *, lines=("Bonjour à tous.", "La réunion d'Oriana commence."), path=None, status="COMPLETED"):
    settings = main.settings
    with session() as db:
        db.add(Video(
            id=video_id, filename=f"{video_id}.mp4", original_filename=f"{video_id}.mp4",
            path=str(path or settings.uploads_dir / f"{video_id}.mp4"), duration_seconds=12.0, size_bytes=1,
            status=status, detected_language="fr", created_at=NOW,
            transcript_text="\n".join(f"[00:00:0{i}] {text}" for i, text in enumerate(lines)),
        ))
        db.flush()
        db.add_all([
            TranscriptSegment(video_id=video_id, start_seconds=float(i), end_seconds=float(i + 1), text=text)
            for i, text in enumerate(lines)
        ])
        db.commit()


def segment_ids(session, video_id="v"):
    with session() as db:
        return [s.id for s in db.query(TranscriptSegment).filter_by(video_id=video_id).order_by(TranscriptSegment.id)]


def make_media(path: Path, *, video=True, seconds=2):
    inputs = ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    if video:
        inputs += ["-f", "lavfi", "-i", f"color=c=black:s=64x64:d={seconds}"]
    subprocess.run(["ffmpeg", "-y", "-nostdin", *inputs, "-shortest", str(path)], check=True, capture_output=True)
    return path


# --- n°2: the glossary learns from corrections ---------------------------------------------

@pytest.mark.parametrize("old, new, expected", [
    ("La réunion d'Oriana commence.", "La réunion de Doñana commence.", [("d'Oriana", "Doñana")]),
    ("On parle des okr du trimestre.", "On parle des OKR du trimestre.", [("okr", "OKR")]),
    ("Il déploie sur cubernetesse.", "Il déploie sur Kubernetes.", [("cubernetesse", "Kubernetes")]),
    ("Rendez-vous à doña na demain.", "Rendez-vous à Doñana demain.", [("doña na", "Doñana")]),
    ("Visite du parque national.", "Visite du Parc national de Doñana.", [("parque national", "Parc national de Doñana")]),
    # Ordinary words: not glossary material.
    ("Un vert d'eau.", "Un verre d'eau.", []),
    # A rewritten sentence is not a misheard name.
    ("un deux trois quatre cinq six sept huit", "Tout à fait autre chose ici pour Paris", []),
    # The term was already there: only its surroundings changed.
    ("le Doñana", "Doñana", []),
])
def test_corrections_between_finds_the_misheard_terms(old, new, expected):
    assert learning.corrections_between(old, new) == expected


def test_replacement_pair_keeps_the_term_of_a_replace_all():
    assert learning.replacement_pair("d'Oriana", "Doñana") == ("d'Oriana", "Doñana")
    assert learning.replacement_pair("vert", "verre") is None


def test_a_term_corrected_twice_is_suggested_then_accepted(client, env):
    add_video(env, "a")
    add_video(env, "b")
    first, second = segment_ids(env, "a")[1], segment_ids(env, "b")[1]
    client.patch(f"/videos/a/segments/{first}", json={"text": "La réunion de Doñana commence."})
    # Once is not enough.
    assert client.get("/glossary/suggestions").json() == []
    client.patch(f"/videos/b/segments/{second}", json={"text": "La réunion de Doñana commence."})
    assert client.get("/glossary/suggestions").json() == [
        {"term": "Doñana", "variants": ["d'Oriana"], "occurrences": 2, "videos": 2}
    ]
    assert client.post("/glossary/suggestions/accept", json={"term": "Doñana"}).json() == {"terms": ["Doñana"]}
    assert client.get("/glossary/suggestions").json() == []
    # Accepting twice changes nothing.
    assert client.post("/glossary/suggestions/accept", json={"term": "doñana"}).json() == {"terms": ["Doñana"]}


def test_a_replace_all_counts_every_occurrence(client, env):
    add_video(env, lines=("okr un.", "okr deux.", "okr trois."))
    assert client.post("/videos/v/transcript/replace", json={"find": "okr", "replace": "OKR", "whole_word": True}).json()["replaced"] == 3
    suggestion = client.get("/glossary/suggestions").json()[0]
    assert (suggestion["term"], suggestion["occurrences"], suggestion["variants"]) == ("OKR", 3, ["okr"])


def test_a_dismissed_suggestion_never_comes_back(client, env):
    add_video(env, lines=("okr un.", "okr deux."))
    client.post("/videos/v/transcript/replace", json={"find": "okr", "replace": "OKR"})
    assert client.post("/glossary/suggestions/dismiss", json={"term": "OKR"}).json() == {"dismissed": True}
    assert client.post("/glossary/suggestions/dismiss", json={"term": "okr"}).status_code == 200
    assert client.get("/glossary/suggestions").json() == []


def test_learnt_corrections_outlive_their_video(client, env):
    add_video(env, lines=("okr un.", "okr deux."))
    client.post("/videos/v/transcript/replace", json={"find": "okr", "replace": "OKR"})
    assert client.delete("/videos/v").status_code == 200
    with env() as db:
        assert [(c.video_id, c.term) for c in db.query(TermCorrection)] == [(None, "OKR")]
    assert client.get("/glossary/suggestions").json()[0]["videos"] == 0


def test_accepting_into_a_full_glossary_is_refused(client, env, monkeypatch):
    monkeypatch.setattr(main, "GLOSSARY_MAX_TERMS", 1)
    with env() as db:
        db.add(GlossaryTerm(term="Existant", position=0))
        db.commit()
    assert client.post("/glossary/suggestions/accept", json={"term": "Doñana"}).status_code == 409


# --- n°14: disk space ------------------------------------------------------------------------

def test_storage_report_measures_each_video(client, env):
    settings = main.settings
    add_video(env, "big")
    add_video(env, "small")
    (settings.uploads_dir / "big.mp4").write_bytes(b"x" * 5000)
    (settings.audio_dir / "big.wav").write_bytes(b"x" * 3000)
    (settings.exports_dir / "big").mkdir()
    (settings.exports_dir / "big" / "summary.md").write_bytes(b"x" * 10)
    (settings.uploads_dir / "small.mp4").write_bytes(b"x" * 100)
    report = client.get("/storage").json()
    assert [row["id"] for row in report["videos"]] == ["big", "small"]
    big = report["videos"][0]
    assert (big["source_bytes"], big["audio_bytes"], big["exports_bytes"], big["total_bytes"]) == (5000, 3000, 10, 8010)
    assert big["can_compact"] and not big["busy"]
    assert report["totals"]["total_bytes"] == 8110
    assert report["disk_free_bytes"] > 0


def test_deleting_the_media_keeps_the_text(client, env):
    settings = main.settings
    add_video(env)
    (settings.uploads_dir / "v.mp4").write_bytes(b"x" * 500)
    (settings.audio_dir / "v.wav").write_bytes(b"x" * 300)
    response = client.post("/videos/v/storage", json={"action": "delete_media"}).json()
    assert response["freed_bytes"] == 800
    assert not (settings.uploads_dir / "v.mp4").exists() and not (settings.audio_dir / "v.wav").exists()
    detail = client.get("/videos/v").json()
    assert detail["transcript_text"] and not detail["source_available"] and not detail["audio_available"]


def test_the_work_audio_goes_only_when_the_source_remains(client, env):
    settings = main.settings
    add_video(env)
    (settings.audio_dir / "v.wav").write_bytes(b"x" * 300)
    # Without its source, the WAV is the only way to listen.
    assert client.post("/videos/v/storage", json={"action": "delete_work_audio"}).status_code == 409
    (settings.uploads_dir / "v.mp4").write_bytes(b"x")
    assert client.post("/videos/v/storage", json={"action": "delete_work_audio"}).json()["freed_bytes"] == 300
    assert (settings.uploads_dir / "v.mp4").exists()


def test_storage_actions_are_refused_when_they_make_no_sense(client, env):
    settings = main.settings
    add_video(env, "queued", status="QUEUED")
    assert client.post("/videos/queued/storage", json={"action": "delete_media"}).status_code == 409
    add_video(env, "mp3", path=settings.uploads_dir / "mp3.mp3")
    (settings.uploads_dir / "mp3.mp3").write_bytes(b"x")
    assert client.post("/videos/mp3/storage", json={"action": "audio"}).json()["detail"] == "La source est déjà un fichier audio compressé"
    assert client.post("/videos/mp3/storage", json={"action": "shred"}).status_code == 422
    assert client.post("/videos/missing/storage", json={"action": "audio"}).status_code == 404


def test_keeping_only_the_audio_queues_a_compact_job(client, env):
    add_video(env)
    (main.settings.uploads_dir / "v.mp4").write_bytes(b"x")
    job = client.post("/videos/v/storage", json={"action": "audio"}).json()["job"]
    assert (job["kind"], job["status"]) == ("COMPACT", "QUEUED")
    # The video is busy until the conversion ends: no second action, no edit.
    assert client.post("/videos/v/storage", json={"action": "delete_media"}).status_code == 409


def test_the_compact_job_replaces_the_video_by_a_small_audio_file(env):
    settings = main.settings
    source = make_media(settings.uploads_dir / "v.mp4")
    (settings.audio_dir / "v.wav").write_bytes(b"x" * 300)
    add_video(env, path=source)
    with env() as db:
        db.add(ProcessingJob(id="j", video_id="v", kind="COMPACT", stage="QUEUED", status="QUEUED", progress=0))
        db.commit()
    worker.run_compact("j")
    with env() as db:
        video = db.get(Video, "v")
        assert db.get(ProcessingJob, "j").status == "COMPLETED"
        assert video.path.endswith("v.m4a") and Path(video.path).stat().st_size > 0
    assert not source.exists() and not (settings.audio_dir / "v.wav").exists()


def test_a_failed_compact_job_leaves_the_video_untouched(env):
    source = main.settings.uploads_dir / "v.mp4"
    source.write_bytes(b"not a video")
    add_video(env, path=source)
    with env() as db:
        db.add(ProcessingJob(id="j", video_id="v", kind="COMPACT", stage="QUEUED", status="QUEUED", progress=0))
        db.commit()
    worker.run_compact("j")
    with env() as db:
        assert db.get(ProcessingJob, "j").status == "FAILED"
        assert db.get(Video, "v").path == str(source)
    assert source.exists()
    assert not list(main.settings.uploads_dir.glob(".*"))


@pytest.mark.parametrize("policy", ["audio", "delete"])
def test_the_media_rule_chosen_at_import_runs_after_processing(env, policy):
    settings = main.settings
    source = make_media(settings.uploads_dir / "v.mp4")
    add_video(env, path=source)
    with env() as db:
        db.get(Video, "v").source_policy = policy
        db.add(ProcessingJob(id="j", video_id="v", stage="INDEXING", status="RUNNING", progress=98))
        db.commit()
    worker._apply_source_policy("j", "v")
    with env() as db:
        path = Path(db.get(Video, "v").path)
    assert not source.exists()
    assert path.suffix == (".m4a" if policy == "audio" else ".mp4")
    assert path.exists() == (policy == "audio")


def test_a_compact_audio_track_is_served_as_audio(client, env):
    source = main.settings.uploads_dir / "v.m4a"
    source.write_bytes(b"aac")
    add_video(env, path=source)
    response = client.get("/videos/v/media")
    assert response.headers["content-type"] == "audio/mp4"
    assert client.get("/videos/v").json()["media_kind"] == "audio"


def test_upload_stores_the_media_rule(client, env):
    files = {"file": ("clip.mp4", b"media", "video/mp4")}
    assert client.post("/videos", files=files, data={"source_policy": "shred"}).status_code == 422
    video_id = client.post("/videos", files=files, data={"source_policy": "audio"}).json()["video_id"]
    assert client.get(f"/videos/{video_id}").json()["source_policy"] == "audio"


# --- n°9: watched folder ---------------------------------------------------------------------

class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def enable_watch_folder(session, **options):
    with session() as db:
        app_settings.save(db, app_settings.WATCH_FOLDER, WatchFolderSettings(enabled=True, **options))
        db.commit()


def test_a_stable_file_is_imported_with_the_defaults(client, env):
    enable_watch_folder(env, summary_length="short", tag="Dossier surveillé", source_policy="audio")
    inbox = main.settings.inbox_dir
    (inbox / "Réunion lundi.mp4").write_bytes(b"media")
    clock = Clock()
    watcher = watch_folder.InboxWatcher(clock)
    assert watcher.scan() == []  # first seen
    clock.now += 3
    assert watcher.scan() == []  # not stable long enough
    clock.now += main.settings.watch_stable_seconds
    assert watcher.scan() == ["Réunion lundi.mp4"]
    assert not (inbox / "Réunion lundi.mp4").exists()
    with env() as db:
        video = db.query(Video).one()
        job = db.query(ProcessingJob).one()
        assert (video.original_filename, video.source_policy, [t.name for t in video.tags]) == ("Réunion lundi.mp4", "audio", ["Dossier surveillé"])
        assert job.summary_length == "short"
        assert Path(video.path).parent == main.settings.uploads_dir and Path(video.path).exists()


def test_a_growing_file_waits(env):
    enable_watch_folder(env)
    path = main.settings.inbox_dir / "copie.mp4"
    path.write_bytes(b"a")
    clock = Clock()
    watcher = watch_folder.InboxWatcher(clock)
    watcher.scan()
    clock.now += 60
    path.write_bytes(b"ab")  # still being copied
    os.utime(path, ns=(1, 2))
    assert watcher.scan() == []
    clock.now += 60
    assert watcher.scan() == ["copie.mp4"]


def test_a_refused_file_is_set_aside_with_its_reason(client, env):
    enable_watch_folder(env)
    inbox = main.settings.inbox_dir
    (inbox / "notes.docx").write_bytes(b"text")
    (inbox / "film.mp4.part").write_bytes(b"partial")
    (inbox / ".cache").write_bytes(b"hidden")
    clock = Clock()
    watcher = watch_folder.InboxWatcher(clock)
    watcher.scan()
    clock.now += 60
    assert watcher.scan() == []
    state = client.get("/watch-folder").json()
    assert [(f["name"], f["reason"]) for f in state["rejected"]] == [("notes.docx", "Format de fichier non pris en charge")]
    assert sorted(f["name"] for f in state["pending"]) == []
    assert (inbox / "film.mp4.part").exists() and (inbox / ".cache").exists()
    # Back to the inbox, then deleted for good.
    assert client.post("/watch-folder/rejected/notes.docx/retry").json() == {"retried": True}
    assert (inbox / "notes.docx").exists() and client.get("/watch-folder").json()["rejected"] == []
    watcher.scan()
    clock.now += 60
    watcher.scan()
    assert client.delete("/watch-folder/rejected/notes.docx").json() == {"deleted": True}
    assert client.delete("/watch-folder/rejected/absent.mp4").status_code == 404
    assert client.delete("/watch-folder/rejected/notes.docx.motif.txt").status_code == 404
    for name in ("..", "a/b", "a\\b"):
        with pytest.raises(FileNotFoundError):
            watch_folder.rejected_path(name)


def test_a_file_waits_while_the_queue_is_down(env, monkeypatch):
    enable_watch_folder(env)
    monkeypatch.setattr(main, "Queue", FailingQueue)
    path = main.settings.inbox_dir / "clip.mp4"
    path.write_bytes(b"media")
    clock = Clock()
    watcher = watch_folder.InboxWatcher(clock)
    watcher.scan()
    clock.now += 60
    assert watcher.scan() == []
    assert path.exists() and not watch_folder.rejected_files()
    monkeypatch.setattr(main, "Queue", SuccessfulQueue)
    clock.now += 10
    assert watcher.scan() == []  # retried later, not at every tick
    clock.now += watch_folder.RETRY_AFTER_SECONDS
    assert watcher.scan() == ["clip.mp4"]


def test_a_disabled_watch_folder_imports_nothing(env):
    (main.settings.inbox_dir / "clip.mp4").write_bytes(b"media")
    clock = Clock()
    watcher = watch_folder.InboxWatcher(clock)
    watcher.scan()
    clock.now += 60
    assert watcher.scan() == []


def test_watch_folder_settings_are_validated(client, env):
    assert client.get("/settings/watch-folder").json()["enabled"] is False
    assert client.put("/settings/watch-folder", json={"enabled": True, "summary_length": "huge"}).status_code == 422
    assert client.put("/settings/watch-folder", json={"enabled": True, "template_id": "missing"}).status_code == 404
    assert client.put("/settings/watch-folder", json={"enabled": True, "source_policy": "shred"}).status_code == 422
    saved = client.put("/settings/watch-folder", json={
        "enabled": True, "tag": "  Réunions   hebdo ", "source_language": "FR", "num_speakers": 3,
    }).json()
    assert (saved["tag"], saved["source_language"], saved["num_speakers"]) == ("Réunions hebdo", "fr", None)
    assert client.get("/settings/watch-folder").json() == saved


# --- n°13: backups ---------------------------------------------------------------------------

@pytest.fixture
def fake_dump(monkeypatch):
    calls = []

    def dump(connection, destination):
        calls.append(connection["database"])
        Path(destination).write_bytes(b"PGDMP fake")

    monkeypatch.setattr(backups, "_dump", dump)
    return calls


def test_a_backup_is_written_listed_downloaded_and_deleted(client, env, fake_dump):
    created = client.post("/backups").json()
    assert created["kind"] == "manuel" and created["name"].startswith("steno-") and created["size_bytes"] == 10
    listing = client.get("/backups").json()
    assert [b["name"] for b in listing["backups"]] == [created["name"]]
    assert listing["settings"] == {"enabled": True, "interval_hours": 24, "keep": 7}
    assert client.get(f"/backups/{created['name']}").content == b"PGDMP fake"
    assert client.get("/backups/..%2F..%2Fetc%2Fpasswd").status_code == 404
    assert client.delete(f"/backups/{created['name']}").json() == {"deleted": True}
    assert client.get("/backups").json()["backups"] == []


def test_a_failed_dump_leaves_no_partial_file(client, env, monkeypatch):
    def broken(connection, destination):
        Path(destination).write_bytes(b"half")
        raise subprocess.CalledProcessError(1, "pg_dump", stderr=b"connection refused")

    monkeypatch.setattr(backups, "_dump", broken)
    assert client.post("/backups").status_code == 500
    assert [path.name for path in main.settings.backups_dir.iterdir() if path.name != backups.LOCK_FILE] == []


def test_only_scheduled_backups_are_pruned(env, fake_dump):
    folder = main.settings.backups_dir
    for day in range(1, 6):
        (folder / f"steno-202609{day:02d}-030000-auto.dump").write_bytes(b"x")
    (folder / "steno-20260901-120000-manuel.dump").write_bytes(b"x")
    (folder / "videoai-20260801-120000.dump").write_bytes(b"x")  # the former `make backup`
    assert backups.prune(2) == [f"steno-202609{day:02d}-030000-auto.dump" for day in (3, 2, 1)]
    assert sorted(path.name for path in folder.glob("*.dump")) == [
        "steno-20260901-120000-manuel.dump", "steno-20260904-030000-auto.dump",
        "steno-20260905-030000-auto.dump", "videoai-20260801-120000.dump",
    ]


def test_a_scheduled_backup_runs_when_due(env, fake_dump):
    now = datetime.now(timezone.utc)
    assert backups.run_scheduled(now) is not None  # none yet
    assert backups.run_scheduled(now) is None  # the latest is fresh
    later = now + timedelta(hours=25)
    assert backups.due(BackupSettings(), later)
    assert not backups.due(BackupSettings(enabled=False), later)
    assert not backups.due(BackupSettings(interval_hours=48), later)


def test_a_failed_scheduled_backup_is_retried_later(client, env, monkeypatch):
    monkeypatch.setattr(backups, "_dump", lambda connection, destination: (_ for _ in ()).throw(subprocess.TimeoutExpired("pg_dump", 1)))
    assert backups.run_scheduled() is None
    assert client.get("/backups").json()["last_error"] == "La sauvegarde a dépassé le délai autorisé"
    assert not backups.due(BackupSettings())  # not on every tick


def test_backup_settings_are_validated(client, env):
    assert client.put("/settings/backups", json={"enabled": True, "interval_hours": 12, "keep": 0}).status_code == 422
    assert client.put("/settings/backups", json={"enabled": False, "interval_hours": 12, "keep": 3}).status_code == 200
    assert client.get("/backups").json()["settings"] == {"enabled": False, "interval_hours": 12, "keep": 3}


def test_restore_refuses_while_services_use_the_database(env, monkeypatch, fake_dump):
    (main.settings.backups_dir / "steno-20260901-120000-auto.dump").write_bytes(b"x")
    monkeypatch.setattr(backups, "other_sessions", lambda connection, database: 2)
    with pytest.raises(backups.BackupError, match="docker compose stop"):
        backups.restore("steno-20260901-120000-auto.dump")
    with pytest.raises(backups.BackupError, match="introuvable"):
        backups.restore("absent.dump")
    assert fake_dump == []  # no safety copy either


# --- n°13: portable library archive ----------------------------------------------------------

def rich_library(session, *, media: Path | None = None):
    add_video(session, "v", path=media)
    with session() as db:
        db.add(SummaryTemplate(id="t1", name="Comité", prompt="# Décisions", is_default=True))
        db.add(GlossaryTerm(term="Doñana", position=0))
        db.flush()
        speaker = Speaker(video_id="v", position=1, name="Alice")
        db.add(speaker)
        db.flush()
        db.query(TranscriptSegment).filter_by(video_id="v").first().speaker_id = speaker.id
        db.add(Summary(id="s1", video_id="v", template_id="t1", content_markdown="# Résumé", model="m", created_at=NOW))
        db.add(LibraryConversation(id="c1", title="Budget ?", video_ids=json.dumps(["v", "gone"]), created_at=NOW, updated_at=NOW))
        db.flush()
        db.add(LibraryMessage(id="m1", conversation_id="c1", role="user", content="Budget ?", created_at=NOW))
        db.commit()
    main.set_video_tags("v", main.TagsIn(tags=["Finance"]))


def archive_bytes(include_media: bool) -> bytes:
    return b"".join(portable.export_stream(include_media))


def test_the_archive_holds_the_library(env):
    media = main.settings.uploads_dir / "v.mp4"
    media.write_bytes(b"video bytes")
    rich_library(env, media=media)
    add_video(env, "pending", status="QUEUED")
    with tarfile.open(fileobj=io.BytesIO(archive_bytes(True))) as archive:
        names = archive.getnames()
        assert names[0] == "steno-library.json" and names[-1] == "conversations.json"
        assert "videos/pending/video.json" not in names  # not processed: nothing to carry
        assert archive.extractfile("videos/v/source.mp4").read() == b"video bytes"
        document = json.loads(archive.extractfile("videos/v/video.json").read())
        assert document["tags"] == ["Finance"] and document["summaries"][0]["template_name"] == "Comité"
        assert document["segments"][0]["speaker"] == 1
    with tarfile.open(fileobj=io.BytesIO(archive_bytes(False))) as archive:
        assert "videos/v/source.mp4" not in archive.getnames()


def test_an_archive_is_merged_into_another_library(client, env, tmp_path, monkeypatch):
    media = main.settings.uploads_dir / "v.mp4"
    media.write_bytes(b"video bytes")
    rich_library(env, media=media)
    archive = tmp_path / "library.tar"
    archive.write_bytes(archive_bytes(True))

    # Another installation: an empty database and data folder.
    other = make_session(tmp_path / "other.db")
    for module in MODULES:
        monkeypatch.setattr(module, "SessionLocal", other)
    monkeypatch.setattr(main.settings, "data_dir", tmp_path / "other-data")
    main.settings.uploads_dir.mkdir(parents=True)
    indexed = []
    monkeypatch.setattr(worker, "enqueue_index_job", indexed.append)

    with archive.open("rb") as handle:
        report = client.post("/library/import", files={"file": ("library.tar", handle, "application/x-tar")}).json()
    assert report == {"videos_added": 1, "videos_skipped": 0, "media_added": 1, "templates_added": 1, "glossary_added": 1,
                      "conversations_added": 1, "errors": []}
    assert indexed == ["v"]
    detail = client.get("/videos/v").json()
    assert detail["status"] == "COMPLETED" and detail["source_available"] and detail["tags"] == ["Finance"]
    assert detail["speakers"][0]["label"] == "Alice" and detail["segments"][0]["speaker_id"] == detail["speakers"][0]["id"]
    assert detail["summaries"][0]["template_name"] == "Comité"
    assert (main.settings.exports_dir / "v" / "summary.md").exists()
    conversation = client.get("/library/conversations/c1").json()
    assert conversation["video_ids"] == ["v"] and conversation["messages"][0]["content"] == "Budget ?"
    # A second import adds nothing.
    report = portable.import_archive(archive)
    assert (report["videos_added"], report["videos_skipped"], report["conversations_added"]) == (0, 1, 0)


def test_a_foreign_file_is_not_imported(client, env):
    response = client.post("/library/import", files={"file": ("notes.tar", b"not a tar at all", "application/x-tar")})
    assert response.status_code == 422
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        data = json.dumps({"format": "steno-library", "version": 99}).encode()
        info = tarfile.TarInfo("steno-library.json")
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    response = client.post("/library/import", files={"file": ("new.tar", buffer.getvalue(), "application/x-tar")})
    assert response.status_code == 422 and "plus récente" in response.json()["detail"]
    assert list(main.settings.uploads_dir.iterdir()) == []


def test_the_export_endpoint_streams_a_tar(client, env):
    rich_library(env)
    response = client.get("/library/export")
    assert response.headers["content-type"] == "application/x-tar"
    assert "steno-bibliotheque-" in response.headers["content-disposition"]
    with tarfile.open(fileobj=io.BytesIO(response.content)) as archive:
        assert "videos/v/video.json" in archive.getnames()
