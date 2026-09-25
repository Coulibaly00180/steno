"""Jobs left RUNNING by a worker that stopped (killed, container restarted).

With several workers, « every RUNNING job » is no longer « interrupted »: the
other worker may be running it. A job is orphaned when no live RQ worker has
it as its current job. RQ drops a worker from its registry once its
heartbeat key expires (~90 s, see worker_entry.WORKER_TTL_SECONDS), so an
orphan is found within about two minutes. The check runs at each worker start
and every minute in the scheduler service.
"""
import logging
from datetime import datetime, timedelta, timezone

from rq import Worker
from sqlalchemy import select

from .db import SessionLocal
from .models import ProcessingJob, QualityRun, Video

logger = logging.getLogger(__name__)

ERROR_PROCESSING_INTERRUPTED = "Traitement interrompu avant sa fin"
TERMINAL_VIDEO_STATUSES = {"COMPLETED", "FAILED", "CANCELLED"}
# A job claimed a moment ago may not be registered as its worker's current job yet.
GRACE_SECONDS = 120


def _aware(value: datetime | None) -> datetime | None:
    return value if value is None or value.tzinfo else value.replace(tzinfo=timezone.utc)


def running_rq_jobs(redis) -> set[str] | None:
    """Ids of the RQ jobs the live workers are running; None when Redis cannot tell."""
    try:
        return {job_id for worker in Worker.all(connection=redis) if (job_id := worker.get_current_job_id())}
    except Exception:
        logger.warning("Unable to read the RQ workers: no job recovered", exc_info=True)
        return None


def fail_running_jobs(*, running_elsewhere: set[str] | None = None, started_before: datetime | None = None,
                      session_factory=None) -> int:
    """Fail the RUNNING jobs, except those `running_elsewhere` (their RQ id) or started after `started_before`.

    Without arguments, every RUNNING job: the single-worker rule, kept for tests.
    The video of a failed FULL job leaves PROCESSING; a finished video stays as it is.
    """
    now = datetime.now(timezone.utc)
    with (session_factory or SessionLocal)() as db:
        jobs = list(db.scalars(select(ProcessingJob).where(ProcessingJob.status == "RUNNING").with_for_update()))
        failed = 0
        for job in jobs:
            if running_elsewhere is not None and job.rq_job_id in running_elsewhere:
                continue
            started = _aware(job.started_at)
            if started_before is not None and started is not None and started > started_before:
                continue
            video = db.scalar(select(Video).where(Video.id == job.video_id).with_for_update())
            job.stage = "FAILED"
            job.status = "FAILED"
            job.error = ERROR_PROCESSING_INTERRUPTED
            job.finished_at = now
            if video and video.status not in TERMINAL_VIDEO_STATUSES:
                video.status = "FAILED"
            failed += 1
        runs = list(db.scalars(select(QualityRun).where(QualityRun.status == "RUNNING")))
        for run in runs:
            if running_elsewhere is not None and run.rq_job_id in running_elsewhere:
                continue
            created = _aware(run.created_at)
            if started_before is not None and created is not None and created > started_before:
                continue
            run.status, run.error, run.current, run.finished_at = "FAILED", "Interrompu (worker arrêté)", None, now
            failed += 1
        db.commit()
    return failed


def recover_orphaned_jobs(redis, *, session_factory=None) -> int:
    """Fail the RUNNING jobs no live worker runs; returns how many. Nothing when Redis cannot tell."""
    running = running_rq_jobs(redis)
    if running is None:
        return 0
    count = fail_running_jobs(
        running_elsewhere=running, started_before=datetime.now(timezone.utc) - timedelta(seconds=GRACE_SECONDS),
        session_factory=session_factory,
    )
    if count:
        logger.warning("Recovered %d job(s) left running by a stopped worker", count)
    return count
