import logging

from redis import Redis
from rq import Queue, Worker

from . import quality
from .config import INDEX_QUEUE_NAME, QUEUE_NAME, settings
from .db import engine
from .schema import assert_schema_current
from .recovery import recover_orphaned_jobs
from .worker import enqueue_missing_entities, enqueue_missing_indexes, mark_interrupted_job

logger = logging.getLogger(__name__)

# The worker's Redis key expires worker_ttl + 60 s after its last heartbeat
# (every job_monitoring_interval = 30 s while busy, every worker_ttl - 15 s
# while idle): /status then reports a stopped worker within about 90 s,
# instead of the RQ default of 480 s.
WORKER_TTL_SECONDS = 30


def handle_work_horse_killed(job, retpid, ret_val, rusage):
    """Persist an RQ hard-timeout/child-kill as a terminal pipeline failure."""
    if not job.args:
        return
    mark_interrupted_job(str(job.args[0]))


# Held by the first worker to start: the others skip the catch-ups (no job queued twice).
STARTUP_KEY = "steno:worker-startup"
STARTUP_SECONDS = 120


def catch_up() -> None:
    """Background work to queue after a start or an upgrade; done once, by the first worker up."""
    try:
        # Videos processed before the semantic search existed, or whose index
        # failed or went stale: indexed in the background, after any analysis.
        queued = enqueue_missing_indexes()
        if queued:
            logger.info("Queued %d video(s) for semantic indexing", queued)
    except Exception:
        logger.warning("Unable to queue the indexing catch-up", exc_info=True)
    try:
        # People, organisations, places and dates (n°16) of the videos processed before, or edited.
        queued = enqueue_missing_entities()
        if queued:
            logger.info("Queued %d video(s) for entity extraction", queued)
    except Exception:
        logger.warning("Unable to queue the entity catch-up", exc_info=True)
    try:
        # Reference corpus (n°4): changed prompts or models start a new run.
        if quality.maybe_schedule("auto"):
            logger.info("Queued a quality run: prompts or models changed since the last one")
    except Exception:
        logger.warning("Unable to schedule the quality run", exc_info=True)


def main():
    assert_schema_current(engine)
    redis = Redis.from_url(settings.redis_url)
    if settings.recover_interrupted_jobs_on_startup:
        # Only the jobs no live worker runs: with several workers, the others keep theirs.
        recover_orphaned_jobs(redis)
    try:
        first = redis.set(STARTUP_KEY, "1", nx=True, ex=STARTUP_SECONDS)
    except Exception:
        logger.warning("Redis unavailable at startup", exc_info=True)
        first = True
    if first:
        catch_up()
    worker = Worker(
        # Listed in priority order: RQ always takes the first non-empty queue.
        [Queue(QUEUE_NAME, connection=redis), Queue(INDEX_QUEUE_NAME, connection=redis)],
        connection=redis,
        work_horse_killed_handler=handle_work_horse_killed,
        worker_ttl=WORKER_TTL_SECONDS,
    )
    worker.work(with_scheduler=False)


if __name__ == "__main__":
    main()
