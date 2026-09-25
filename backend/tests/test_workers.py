"""Several workers in parallel: the transcription slot, orphaned jobs, queue estimates, startup."""
import os
import threading
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app import gpu_slot, recovery, status, worker, worker_entry
from app.models import ProcessingJob, QualityRun, Video
from app.queue_info import QueueInfo, queue_snapshot
from tests.test_phase3 import NOW, add_durations, add_video, db_session  # noqa: F401 (fixture)

redis_only = pytest.mark.skipif(os.environ.get("RUN_REDIS_TESTS") != "1", reason="needs the test stack's Redis")


# --- the transcription slot -------------------------------------------------------------------


@pytest.fixture
def redis_client():
    from redis import Redis

    client = Redis.from_url(os.environ["REDIS_URL"])
    yield client
    client.delete("test:slot")


@redis_only
def test_transcriptions_take_turns(redis_client, monkeypatch):
    monkeypatch.setattr(gpu_slot, "POLL_SECONDS", 0.05)
    events = []
    first_in = threading.Event()
    release_first = threading.Event()

    def first():
        with gpu_slot.transcription_slot(redis=redis_client, key="test:slot"):
            events.append("first in")
            first_in.set()
            release_first.wait(5)
            events.append("first out")

    def second():
        first_in.wait(5)
        with gpu_slot.transcription_slot(redis=redis_client, key="test:slot", on_wait=lambda: events.append("second waits")):
            events.append("second in")

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    time.sleep(0.3)
    release_first.set()
    for thread in threads:
        thread.join(5)
    assert events == ["first in", "second waits", "first out", "second in"]
    assert redis_client.get("test:slot") is None


@redis_only
def test_a_cancelled_job_stops_waiting(redis_client, monkeypatch):
    monkeypatch.setattr(gpu_slot, "POLL_SECONDS", 0.05)
    redis_client.set("test:slot", "another worker", ex=60)

    def cancelled():
        raise worker.JobCancelled("j")

    with pytest.raises(worker.JobCancelled):
        with gpu_slot.transcription_slot(redis=redis_client, key="test:slot", check=cancelled):
            pytest.fail("the slot was taken")
    # Never taken from its holder.
    assert redis_client.get("test:slot") == b"another worker"


@redis_only
def test_a_lost_lease_is_neither_renewed_nor_freed_by_its_former_holder(redis_client, monkeypatch):
    monkeypatch.setattr(gpu_slot, "RENEW_SECONDS", 0.1)
    with gpu_slot.transcription_slot(redis=redis_client, key="test:slot"):
        assert redis_client.ttl("test:slot") > gpu_slot.LEASE_SECONDS - 5
        # The lease expired (the worker was stuck) and another worker took the slot.
        redis_client.set("test:slot", "new holder", ex=60)
        time.sleep(0.3)
        assert redis_client.ttl("test:slot") <= 60
    assert redis_client.get("test:slot") == b"new holder"


@redis_only
def test_a_killed_worker_frees_the_slot_when_its_lease_ends(redis_client, monkeypatch):
    monkeypatch.setattr(gpu_slot, "LEASE_SECONDS", 1)
    monkeypatch.setattr(gpu_slot, "POLL_SECONDS", 0.1)
    redis_client.set("test:slot", "killed worker", ex=1)
    started = time.monotonic()
    with gpu_slot.transcription_slot(redis=redis_client, key="test:slot"):
        pass
    assert time.monotonic() - started < 3


# --- orphaned jobs ------------------------------------------------------------------------------


def running(session, video_id, *, started, rq_id):
    add_video(session, video_id, status="PROCESSING", job_status="RUNNING", started_at=started)
    with session() as db:
        db.get(ProcessingJob, f"job-{video_id}").rq_job_id = rq_id
        db.commit()


def test_only_the_jobs_no_worker_runs_are_failed(db_session, monkeypatch):
    session = db_session
    long_ago = NOW - timedelta(minutes=30)
    running(session, "mine", started=long_ago, rq_id="rq-alive")
    running(session, "orphan", started=long_ago, rq_id="rq-dead")
    with session() as db:
        db.add(QualityRun(id="run", status="RUNNING", scope="quick", trigger="auto", llm_model="m", whisper_model="w",
                          prompt_version="p", fingerprint="f", rq_job_id="rq-dead-run", created_at=long_ago))
        db.commit()
    workers = [SimpleNamespace(get_current_job_id=lambda: "rq-alive"), SimpleNamespace(get_current_job_id=lambda: None)]
    monkeypatch.setattr(recovery.Worker, "all", staticmethod(lambda connection: workers))

    assert recovery.recover_orphaned_jobs("redis", session_factory=session) == 2
    with session() as db:
        # The other worker's job goes on.
        assert db.get(ProcessingJob, "job-mine").status == "RUNNING"
        assert db.get(Video, "mine").status == "PROCESSING"
        assert (db.get(ProcessingJob, "job-orphan").status, db.get(Video, "orphan").status) == ("FAILED", "FAILED")
        assert db.get(QualityRun, "run").status == "FAILED"
    assert recovery.recover_orphaned_jobs("redis", session_factory=session) == 0


def test_a_job_just_claimed_is_left_alone(db_session, monkeypatch):
    from datetime import datetime, timezone

    running(db_session, "fresh", started=datetime.now(timezone.utc), rq_id=None)
    monkeypatch.setattr(recovery.Worker, "all", staticmethod(lambda connection: []))
    assert recovery.recover_orphaned_jobs("redis", session_factory=db_session) == 0


def test_nothing_is_failed_when_redis_cannot_tell(db_session, monkeypatch):
    running(db_session, "v", started=NOW - timedelta(hours=1), rq_id="rq")

    def down(connection):
        raise ConnectionError("redis down")

    monkeypatch.setattr(recovery.Worker, "all", staticmethod(down))
    assert recovery.recover_orphaned_jobs("redis", session_factory=db_session) == 0
    with db_session() as db:
        assert db.get(ProcessingJob, "job-v").status == "RUNNING"


# --- queue estimates ----------------------------------------------------------------------------


def test_two_workers_share_the_queue(db_session):
    session = db_session
    add_durations(session, "FULL", [(600, 60), (1200, 120)])  # 0.1 s per media second
    add_video(session, "run", status="PROCESSING", duration=1200, job_status="RUNNING",
              job_created_at=NOW - timedelta(minutes=5), started_at=NOW - timedelta(seconds=30))
    add_video(session, "q1", status="QUEUED", duration=600, job_status="QUEUED", job_created_at=NOW - timedelta(minutes=4))
    add_video(session, "q2", status="QUEUED", duration=3000, job_status="QUEUED", job_created_at=NOW - timedelta(minutes=3))
    add_video(session, "q3", status="QUEUED", duration=600, job_status="QUEUED", job_created_at=NOW - timedelta(minutes=2))
    with session() as db:
        snapshot = queue_snapshot(db, now=NOW, workers=2)
    # The idle worker takes q1 at once (ends at 60 s), then q2 (60 + 300); the running
    # job ends at 90 s and its worker takes q3 (90 + 60).
    assert snapshot["job-q1"] == QueueInfo(1, pytest.approx(60.0))
    assert snapshot["job-q2"] == QueueInfo(2, pytest.approx(360.0))
    assert snapshot["job-q3"] == QueueInfo(3, pytest.approx(150.0))
    with session() as db:
        # One worker: one after the other, as before.
        assert queue_snapshot(db, now=NOW, workers=1)["job-q3"] == QueueInfo(3, pytest.approx(510.0))


# --- startup and status -------------------------------------------------------------------------


class StartupRedis:
    def __init__(self, first: bool):
        self.first = first

    def set(self, key, value, nx, ex):
        return self.first or None


@pytest.mark.parametrize("first, expected", [(True, ["recover", "catch-up", "work"]), (False, ["recover", "work"])])
def test_only_the_first_worker_up_queues_the_catch_ups(monkeypatch, first, expected):
    calls = []
    monkeypatch.setattr(worker_entry, "assert_schema_current", lambda engine: None)
    monkeypatch.setattr(worker_entry.Redis, "from_url", lambda url: StartupRedis(first))
    monkeypatch.setattr(worker_entry, "recover_orphaned_jobs", lambda redis: calls.append("recover"))
    monkeypatch.setattr(worker_entry, "catch_up", lambda: calls.append("catch-up"))
    monkeypatch.setattr(worker_entry, "Queue", lambda name, connection: name)
    monkeypatch.setattr(worker_entry.settings, "recover_interrupted_jobs_on_startup", True)

    class FakeWorker:
        def __init__(self, queues, **kwargs):
            pass

        def work(self, with_scheduler):
            calls.append("work")

    monkeypatch.setattr(worker_entry, "Worker", FakeWorker)
    worker_entry.main()
    assert calls == expected


def test_the_status_counts_the_workers(monkeypatch):
    class Registry:
        def __init__(self, state):
            self.state = state

        def get_state(self):
            return self.state

        def get_current_job_id(self):
            return "rq-1" if self.state == "busy" else None

    class FakeRedis:
        def ping(self):
            return True

    monkeypatch.setattr(status.Redis, "from_url", lambda *args, **kwargs: FakeRedis())
    monkeypatch.setattr(status, "Queue", lambda *args, **kwargs: None)
    monkeypatch.setattr(status.Worker, "all", staticmethod(lambda queue: [Registry("busy"), Registry("idle")]))
    assert status.check_queue()["worker"]["detail"] == "2 workers · 1 occupé"
    monkeypatch.setattr(status.Worker, "all", staticmethod(lambda queue: [Registry("idle")]))
    assert status.check_queue()["worker"]["detail"] == "inactif"


@pytest.mark.skipif(os.environ.get("RUN_POSTGRES_TESTS") != "1", reason="needs the test stack's PostgreSQL")
def test_a_forked_job_opens_its_own_database_connections():
    """RQ forks a process per job: the child must never speak on the parent's PostgreSQL session."""
    from sqlalchemy import text

    from app.db import engine

    with engine.connect() as connection:
        parent_pid = connection.execute(text("SELECT pg_backend_pid()")).scalar()
    read, write = os.pipe()
    child = os.fork()
    if child == 0:
        try:
            with engine.connect() as connection:
                pid = connection.execute(text("SELECT pg_backend_pid()")).scalar()
            os.write(write, str(pid).encode())
        finally:
            os._exit(0)
    os.waitpid(child, 0)
    child_pid = int(os.read(read, 32))
    # Another server session; and the parent's still works.
    assert child_pid != parent_pid
    with engine.connect() as connection:
        assert connection.execute(text("SELECT pg_backend_pid()")).scalar() == parent_pid
