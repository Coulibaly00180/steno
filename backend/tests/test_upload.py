from types import SimpleNamespace

from app import main
from app.models import ProcessingJob, Video


class SuccessfulQueue:
    def __init__(self, *args, **kwargs):
        pass

    def enqueue(self, *args, **kwargs):
        return SimpleNamespace(id="rq-job-id")


class FailingQueue:
    def __init__(self, *args, **kwargs):
        pass

    def enqueue(self, *args, **kwargs):
        raise ConnectionError("Redis is unavailable")


def upload(client, payload):
    return client.post("/videos", files={"file": ("clip.mp4", payload, "video/mp4")})


def test_upload_rejects_an_unsupported_file_extension(client):
    response = client.post(
        "/videos",
        files={"file": ("notes.txt", b"not a media file", "text/plain")},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Format de fichier non pris en charge"}


def test_upload_under_byte_limit_is_stored_and_enqueued(client, upload_environment, monkeypatch):
    session, uploads_dir = upload_environment
    monkeypatch.setattr(main, "Queue", SuccessfulQueue)

    response = upload(client, b"a" * 15)

    assert response.status_code == 200
    with session() as db:
        assert db.query(Video).count() == 1
        job = db.query(ProcessingJob).one()
        assert job.rq_job_id == "rq-job-id"
    assert [path.stat().st_size for path in uploads_dir.iterdir()] == [15]


def test_upload_at_byte_limit_is_accepted(client, upload_environment, monkeypatch):
    session, uploads_dir = upload_environment
    monkeypatch.setattr(main, "Queue", SuccessfulQueue)

    response = upload(client, b"a" * 16)

    assert response.status_code == 200
    with session() as db:
        assert db.query(Video).count() == 1
    assert [path.stat().st_size for path in uploads_dir.iterdir()] == [16]


def test_upload_over_byte_limit_cleans_partial_file_and_creates_no_records(client, upload_environment):
    session, uploads_dir = upload_environment
    main.settings.max_upload_bytes = main.UPLOAD_CHUNK_SIZE + 1

    # The first chunk is written before the final byte exceeds the limit.
    response = upload(client, b"a" * (main.UPLOAD_CHUNK_SIZE + 2))

    assert response.status_code == 413
    with session() as db:
        assert db.query(Video).count() == 0
        assert db.query(ProcessingJob).count() == 0
    assert list(uploads_dir.iterdir()) == []


def test_invalid_media_is_cleaned_up(client, upload_environment, monkeypatch):
    session, uploads_dir = upload_environment
    monkeypatch.setattr(main, "ffprobe_duration", lambda _, **__: (_ for _ in ()).throw(ValueError("invalid media")))

    response = upload(client, b"valid-size")

    assert response.status_code == 400
    with session() as db:
        assert db.query(Video).count() == 0
        assert db.query(ProcessingJob).count() == 0
    assert list(uploads_dir.iterdir()) == []


def test_enqueue_failure_compensates_database_and_file(client, upload_environment, monkeypatch):
    session, uploads_dir = upload_environment
    monkeypatch.setattr(main, "Queue", FailingQueue)

    response = upload(client, b"valid-size")

    assert response.status_code == 503
    assert response.json() == {"detail": "Service de traitement indisponible, réessayez ultérieurement"}
    with session() as db:
        assert db.query(Video).count() == 0
        assert db.query(ProcessingJob).count() == 0
    assert list(uploads_dir.iterdir()) == []


def test_failed_video_can_be_retried_without_reuploading(client, upload_environment, monkeypatch):
    session, uploads_dir = upload_environment
    monkeypatch.setattr(main, "Queue", SuccessfulQueue)
    source = uploads_dir / "existing.mp4"
    source.write_bytes(b"existing video")
    with session() as db:
        video = Video(
            id="video-retry",
            filename=source.name,
            original_filename="existing.mp4",
            path=str(source),
            duration_seconds=1.0,
            size_bytes=source.stat().st_size,
            status="FAILED",
            transcript_text="Transcription déjà disponible.",
            detected_language="fr",
        )
        previous = ProcessingJob(
            id="job-failed",
            video_id=video.id,
            stage="FAILED",
            status="FAILED",
            progress=56,
            error="Traitement interrompu avant sa fin",
            template_id="template-1",
            custom_prompt="Résumé court",
        )
        db.add(video)
        db.flush()
        db.add(previous)
        db.commit()

    response = client.post("/videos/video-retry/retry")

    assert response.status_code == 200
    retried = response.json()
    assert retried["status"] == "QUEUED"
    assert retried["stage"] == "QUEUED"
    assert retried["rq_job_id"] if "rq_job_id" in retried else True
    with session() as db:
        video = db.get(Video, "video-retry")
        jobs = db.query(ProcessingJob).filter_by(video_id=video.id).all()
        new_job = next(job for job in jobs if job.id == retried["id"])
        assert video.status == "QUEUED"
        assert len(jobs) == 2
        assert new_job.rq_job_id == "rq-job-id"
        assert new_job.template_id == "template-1"
        assert new_job.custom_prompt == "Résumé court"


def test_only_failed_video_can_be_retried(client, upload_environment, monkeypatch):
    session, uploads_dir = upload_environment
    monkeypatch.setattr(main, "Queue", SuccessfulQueue)
    source = uploads_dir / "queued.mp4"
    source.write_bytes(b"queued video")
    with session() as db:
        video = Video(
            id="video-queued",
            filename=source.name,
            original_filename="queued.mp4",
            path=str(source),
            duration_seconds=1.0,
            size_bytes=source.stat().st_size,
            status="QUEUED",
        )
        job = ProcessingJob(id="job-queued", video_id=video.id, stage="QUEUED", status="QUEUED", progress=0)
        db.add(video)
        db.flush()
        db.add(job)
        db.commit()

    response = client.post("/videos/video-queued/retry")

    assert response.status_code == 409
    assert response.json() == {"detail": "Seule une vidéo en erreur ou annulée peut être relancée"}


def test_ogg_formats_are_accepted(client, upload_environment, monkeypatch):
    """Wikimedia Commons and free podcasts publish Ogg: .ogv video, .ogg/.opus audio."""
    session, _ = upload_environment
    monkeypatch.setattr(main, "Queue", SuccessfulQueue)
    monkeypatch.setattr(main.settings, "max_upload_bytes", 1024)
    for name in ("clip.ogv", "podcast.ogg", "voice.opus"):
        response = client.post("/videos", files={"file": (name, b"media", "application/ogg")})
        assert response.status_code == 200, name
    kinds = {video["original_filename"]: client.get(f"/videos/{video['id']}").json()["media_kind"] for video in client.get("/videos").json()}
    assert kinds == {"clip.ogv": "video", "podcast.ogg": "audio", "voice.opus": "audio"}
