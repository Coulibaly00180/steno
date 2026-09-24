import logging

from redis import Redis
from rq import Queue, Worker

from .config import INDEX_QUEUE_NAME, QUEUE_NAME, settings
from .db import engine
from .schema import assert_schema_current
from .worker import enqueue_missing_entities, enqueue_missing_indexes, mark_interrupted_job, recover_interrupted_jobs

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


def main():
    assert_schema_current(engine)
    if settings.recover_interrupted_jobs_on_startup:
        recover_interrupted_jobs()
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
    redis = Redis.from_url(settings.redis_url)
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
