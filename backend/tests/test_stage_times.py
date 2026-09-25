"""Time spent in each stage: recorded by the worker, kept with the duration, reported per hour of media."""
import json
from datetime import datetime, timedelta, timezone

from app import stage_times, worker
from app.models import JobDuration, ProcessingJob
from tests.test_phase3 import NOW, add_video, db_session  # noqa: F401 (fixture)

T0 = datetime(2026, 9, 25, 10, 0, tzinfo=timezone.utc)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def test_a_stage_left_and_reached_again_adds_up():
    raw = None
    for stage, second in [("STARTING", 0), ("TRANSCRIBING", 2), ("WAITING_TRANSCRIPTION", 5), ("TRANSCRIBING", 9),
                          ("TRANSCRIBING", 10), ("SUMMARIZING_FINAL", 30), ("COMPLETED", 40)]:
        raw = stage_times.append(raw, stage, at(second))
    # The same stage twice in a row is one mark.
    assert [stage for stage, _ in json.loads(raw)] == [
        "STARTING", "TRANSCRIBING", "WAITING_TRANSCRIPTION", "TRANSCRIBING", "SUMMARIZING_FINAL", "COMPLETED",
    ]
    assert stage_times.durations(raw, at(40)) == {
        "STARTING": 2.0, "TRANSCRIBING": 24.0, "WAITING_TRANSCRIPTION": 4.0, "SUMMARIZING_FINAL": 10.0,
    }
    assert stage_times.durations(None, at(1)) == {} and stage_times.durations("not json", at(1)) == {}


def test_the_worker_keeps_the_time_of_each_stage(db_session, monkeypatch):
    add_video(db_session, "v", status="PROCESSING", duration=1800, job_status="QUEUED", job_created_at=at(-30))
    # One tick per call to now(): the start, each stage change, then the end of the job.
    clock = iter([at(0), at(0), at(4), at(64), at(94), at(94)])
    monkeypatch.setattr(worker, "now", lambda: next(clock))
    worker._start_job("job-v")
    worker.set_job("job-v", stage="EXTRACTING_AUDIO")
    worker.set_job("job-v", stage="TRANSCRIBING", progress=15)
    worker.set_job("job-v", stage="SUMMARIZING_FINAL", progress=90)
    worker.set_job("job-v", progress=95)  # no stage: nothing added
    worker.set_job("job-v", stage="COMPLETED", status="COMPLETED", progress=100)
    with db_session() as db:
        row = db.query(JobDuration).one()
        assert json.loads(row.stages) == {"STARTING": 0.0, "EXTRACTING_AUDIO": 4.0, "TRANSCRIBING": 60.0, "SUMMARIZING_FINAL": 30.0}
        assert row.queued_seconds == 30.0
        assert len(json.loads(db.get(ProcessingJob, "job-v").stage_times)) == 5


def add_duration(session, media, stages, minutes_ago, total=None):
    with session() as db:
        db.add(JobDuration(kind="FULL", media_seconds=media, elapsed_seconds=total or sum(stages.values()),
                           finished_at=NOW - timedelta(minutes=minutes_ago), stages=json.dumps(stages), queued_seconds=5.0))
        db.commit()


def test_the_report_gives_seconds_per_hour_of_media_and_the_trend(db_session, client):
    # 15 earlier jobs: transcription 60 s per hour of media; then 10 recent jobs twice as slow.
    for index in range(15):
        add_duration(db_session, 1800, {"TRANSCRIBING": 30.0, "SUMMARIZING_FINAL": 10.0}, 100 + index)
    for index in range(10):
        add_duration(db_session, 3600, {"TRANSCRIBING": 120.0, "SUMMARIZING_FINAL": 20.0}, index)
    report = client.get("/performance").json()
    stages = {stage["stage"]: stage for stage in report["stages"]}
    assert report["jobs"] == 25 and report["media_hours"] == 17.5 and report["queued_median_seconds"] == 5.0
    assert (stages["TRANSCRIBING"]["recent"], stages["TRANSCRIBING"]["earlier"]) == (120.0, 60.0)
    assert stages["SUMMARIZING_FINAL"]["recent"] == stages["SUMMARIZING_FINAL"]["earlier"] == 20.0
    assert (report["total_recent"], report["total_earlier"]) == (140.0, 80.0)


def test_too_few_jobs_give_no_median(db_session, client):
    add_duration(db_session, 600, {"TRANSCRIBING": 10.0}, 1)
    report = client.get("/performance").json()
    assert report["jobs"] == 1 and report["stages"][0]["per_media_hour"] is None
    assert client.get("/performance", params={"kind": "OTHER"}).status_code == 422


# --- the library and the actions page by page ---------------------------------------------------


def test_the_library_comes_page_by_page(db_session, client):
    for index in range(7):
        add_video(db_session, f"v{index}", created_at=NOW - timedelta(hours=index), name=f"Réunion {index}.mp4")
    first = client.get("/videos", params={"limit": 3})
    assert first.headers["X-Total-Count"] == "7"
    pages = [first.json()] + [client.get("/videos", params={"limit": 3, "offset": offset}).json() for offset in (3, 6)]
    ids = [video["id"] for page in pages for video in page]
    # Most recent first, each video once, none left out.
    assert ids == [f"v{index}" for index in range(7)]
    assert client.get("/videos", params={"offset": 50}).json() == []
    assert client.get("/videos", params={"limit": 501}).status_code == 422


def test_a_search_is_paged_too(db_session, client):
    for index in range(4):
        add_video(db_session, f"b{index}", created_at=NOW - timedelta(hours=index), transcript="Le budget du trimestre.")
    add_video(db_session, "other", transcript="La météo.")
    response = client.get("/videos", params={"q": "budget", "limit": 2, "offset": 2, "mode": "exact"})
    assert response.headers["X-Total-Count"] == "4"
    assert [video["id"] for video in response.json()] == ["b2", "b3"]


def test_the_actions_come_page_by_page_and_the_exports_stay_whole(db_session, client):
    from app.models import ActionItem

    add_video(db_session, "v", name="Comité.mp4")
    with db_session() as db:
        for index in range(5):
            db.add(ActionItem(id=f"a{index}", video_id="v", kind="action", text=f"Action {index}", status="open",
                              owner="Claire" if index % 2 else "karim", position=index))
        db.commit()
    page = client.get("/actions", params={"limit": 2, "offset": 2}).json()
    assert page["total"] == 5 and [item["text"] for item in page["items"]] == ["Action 2", "Action 3"]
    assert page["owners"] == ["Claire", "karim"]
    csv = client.get("/actions/export.csv").content.decode("utf-8-sig")
    assert all(f"Action {index}" in csv for index in range(5))
