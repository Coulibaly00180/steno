import uuid

from app import main
from app.models import ProcessingJob, Video


def create_video(session, tmp_path, *, job_status="COMPLETED"):
    video_id = str(uuid.uuid4())
    source = tmp_path / f"{video_id}.mp4"
    source.write_bytes(b"media")
    export_dir = tmp_path / "exports" / video_id
    export_dir.mkdir(parents=True)
    (export_dir / "summary.md").write_text("summary")
    with session() as db:
        db.add(Video(
            id=video_id,
            filename=source.name,
            original_filename="clip.mp4",
            path=str(source),
            duration_seconds=1,
            size_bytes=5,
            status="COMPLETED" if job_status == "COMPLETED" else "PROCESSING",
        ))
        db.flush()
        db.add(ProcessingJob(
            id=str(uuid.uuid4()),
            video_id=video_id,
            stage=job_status,
            status=job_status,
            progress=100 if job_status == "COMPLETED" else 1,
        ))
        db.commit()
    return video_id, source, export_dir


def test_delete_missing_video_returns_404(client, upload_environment):
    response = client.delete("/videos/missing")
    assert response.status_code == 404


def test_video_detail_exposes_its_latest_job(client, upload_environment, tmp_path):
    session, _ = upload_environment
    video_id, _, _ = create_video(session, tmp_path, job_status="FAILED")

    response = client.get(f"/videos/{video_id}")

    assert response.status_code == 200
    assert response.json()["job"]["video_id"] == video_id
    assert response.json()["job"]["status"] == "FAILED"


def test_delete_active_job_returns_409(client, upload_environment, tmp_path):
    session, _ = upload_environment
    video_id, source, export_dir = create_video(session, tmp_path, job_status="RUNNING")
    response = client.delete(f"/videos/{video_id}")
    assert response.status_code == 409
    assert source.exists()
    assert export_dir.exists()


def test_delete_terminal_job_cleans_database_and_files(client, upload_environment, tmp_path):
    session, _ = upload_environment
    video_id, source, export_dir = create_video(session, tmp_path, job_status="FAILED")
    response = client.delete(f"/videos/{video_id}")
    assert response.status_code == 200
    assert response.json() == {"deleted": True}
    assert not source.exists()
    assert not export_dir.exists()
    with session() as db:
        assert db.get(Video, video_id) is None
        assert db.query(ProcessingJob).filter_by(video_id=video_id).count() == 0


def test_delete_terminal_job_with_missing_files_then_repeat_is_safe(client, upload_environment, tmp_path):
    session, _ = upload_environment
    video_id, source, export_dir = create_video(session, tmp_path, job_status="COMPLETED")
    source.unlink()
    import shutil
    shutil.rmtree(export_dir)
    first = client.delete(f"/videos/{video_id}")
    second = client.delete(f"/videos/{video_id}")
    assert first.status_code == 200
    assert second.status_code == 404
