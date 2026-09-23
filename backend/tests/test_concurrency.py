import os
import threading
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app import main, worker
from app.migrate import migrate
from app.config import settings
from app.models import ProcessingJob, Video


pytestmark = pytest.mark.integration


@pytest.mark.skipif(
    os.getenv("RUN_POSTGRES_TESTS") != "1",
    reason="set RUN_POSTGRES_TESTS=1 to run PostgreSQL integration tests",
)
def test_worker_claim_blocks_delete_until_running(monkeypatch, tmp_path):
    """A DELETE blocked behind a worker claim observes RUNNING and returns 409."""
    engine = create_engine(settings.database_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    migrate(engine)
    monkeypatch.setattr(main, "SessionLocal", Session)
    monkeypatch.setattr(worker, "SessionLocal", Session)
    monkeypatch.setattr(main.settings, "data_dir", tmp_path)

    video_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    source = tmp_path / f"{video_id}.mp4"
    source.write_bytes(b"media")
    with Session() as db:
        db.add(Video(
            id=video_id, filename=source.name, original_filename="race.mp4",
            path=str(source), duration_seconds=1, size_bytes=5, status="QUEUED",
        ))
        db.commit()
        db.add(ProcessingJob(
            id=job_id, video_id=video_id, stage="QUEUED", status="QUEUED", progress=0,
        ))
        db.commit()

    worker_locked = threading.Event()
    release_worker = threading.Event()
    delete_done = threading.Event()
    result = {}

    def claim_worker_job():
        with Session() as db:
            job = db.scalar(
                select(ProcessingJob)
                .where(ProcessingJob.id == job_id)
                .with_for_update()
            )
            assert job.status == "QUEUED"
            job.status = "RUNNING"
            job.stage = "STARTING"
            worker_locked.set()
            assert release_worker.wait(timeout=5)
            db.commit()

    def attempt_delete():
        with TestClient(main.app) as client:
            response = client.delete(f"/videos/{video_id}")
            result["status"] = response.status_code
        delete_done.set()

    claim_thread = threading.Thread(target=claim_worker_job)
    delete_thread = threading.Thread(target=attempt_delete)
    claim_thread.start()
    assert worker_locked.wait(timeout=5)
    delete_thread.start()
    assert not delete_done.wait(timeout=0.25)
    release_worker.set()
    claim_thread.join(timeout=5)
    delete_thread.join(timeout=5)

    assert not claim_thread.is_alive()
    assert not delete_thread.is_alive()
    assert result["status"] == 409
    assert source.exists()
    with Session() as db:
        assert db.get(Video, video_id) is not None
        assert db.get(ProcessingJob, job_id).status == "RUNNING"
        db.delete(db.get(ProcessingJob, job_id))
        db.delete(db.get(Video, video_id))
        db.commit()
    source.unlink(missing_ok=True)
