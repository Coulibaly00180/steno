from types import SimpleNamespace

from app import worker_entry


def test_work_horse_kill_marks_pipeline_failed(monkeypatch):
    received = []
    monkeypatch.setattr(worker_entry, "mark_interrupted_job", received.append)

    worker_entry.handle_work_horse_killed(SimpleNamespace(args=("job-123",)), 1, 9, None)
    worker_entry.handle_work_horse_killed(SimpleNamespace(args=()), 1, 9, None)

    assert received == ["job-123"]


def test_main_checks_schema_then_starts_worker_with_short_ttl(monkeypatch):
    calls = []

    class FirstRedis:
        def set(self, key, value, nx, ex):
            return True

    monkeypatch.setattr(worker_entry, "assert_schema_current", lambda engine: calls.append("schema"))
    monkeypatch.setattr(worker_entry, "recover_orphaned_jobs", lambda redis: calls.append("recover"))
    monkeypatch.setattr(worker_entry, "enqueue_missing_indexes", lambda: calls.append("index") or 0)
    monkeypatch.setattr(worker_entry, "enqueue_missing_entities", lambda: 0)
    monkeypatch.setattr(worker_entry.quality, "maybe_schedule", lambda trigger: None)
    monkeypatch.setattr(worker_entry.Redis, "from_url", lambda url: FirstRedis())
    monkeypatch.setattr(worker_entry, "Queue", lambda name, connection: name)

    class FakeWorker:
        def __init__(self, queues, **kwargs):
            calls.append(("worker", queues, kwargs["worker_ttl"]))

        def work(self, with_scheduler):
            calls.append("work")

    monkeypatch.setattr(worker_entry, "Worker", FakeWorker)
    monkeypatch.setattr(worker_entry.settings, "recover_interrupted_jobs_on_startup", True)

    worker_entry.main()

    # The indexing queue comes second: RQ serves it only when no analysis waits.
    assert calls == [
        "schema", "recover", "index", ("worker", ["video-ai", "video-ai-index"], worker_entry.WORKER_TTL_SECONDS), "work",
    ]
