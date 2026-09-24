"""Queue position and time estimates (n°13).

A single worker runs the jobs in creation order: a queued job waits for the
running job and for every job queued before it. Durations are learnt from the
completed jobs (`JobDuration`), which outlive the deletion of their video, so
the estimates follow the machine: a GPU run and a CPU run differ 20-fold.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .models import JobDuration, ProcessingJob, Video

ACTIVE_JOB_STATUSES = ("QUEUED", "RUNNING")
HISTORY_SIZE = 30
# Below this, a line through the points is noise: fall back to a ratio.
MIN_SAMPLES_FOR_LINE = 3


@dataclass(frozen=True)
class DurationModel:
    """elapsed ≈ fixed + per_media_second × media duration."""

    fixed: float
    per_media_second: float

    def predict(self, media_seconds: float) -> float:
        return self.fixed + self.per_media_second * max(0.0, media_seconds)


def fit_duration_model(samples: list[tuple[float, float]]) -> DurationModel | None:
    """Least-squares line through (media seconds, elapsed seconds).

    A pure ratio is wrong at both ends: on the reference corpus (GPU), a 3-min
    clip took 30 s (0.17 s per media second) and a 3-hour conference 6.5 min
    (0.034). The fixed part (model loading, final summary) explains the gap.
    """
    points = [(media, elapsed) for media, elapsed in samples if media > 0 and elapsed > 0]
    if not points:
        return None
    ratio = DurationModel(0.0, median(elapsed / media for media, elapsed in points))
    if len(points) < MIN_SAMPLES_FOR_LINE:
        return ratio
    mean_media = sum(media for media, _ in points) / len(points)
    mean_elapsed = sum(elapsed for _, elapsed in points) / len(points)
    variance = sum((media - mean_media) ** 2 for media, _ in points)
    if variance == 0:
        return ratio
    slope = sum((media - mean_media) * (elapsed - mean_elapsed) for media, elapsed in points) / variance
    fixed = mean_elapsed - slope * mean_media
    if slope <= 0 or fixed < 0:
        return ratio
    return DurationModel(fixed, slope)


def duration_models(db: Session) -> dict[tuple[str, bool], DurationModel | None]:
    """One model per (job kind, translated); a translation adds a whole LLM pass."""
    rows = db.execute(
        select(JobDuration.kind, JobDuration.translated, JobDuration.media_seconds, JobDuration.elapsed_seconds)
        .order_by(desc(JobDuration.finished_at))
        .limit(HISTORY_SIZE * 8)
    ).all()
    models: dict[tuple[str, bool], DurationModel | None] = {}
    for kind in ("FULL", "SUMMARY", "INDEX", "ENTITIES", "DIARIZE", "COMPACT"):
        same_kind = [row for row in rows if row.kind == kind]
        for translated in (False, True):
            exact = [row for row in same_kind if row.translated == translated][:HISTORY_SIZE]
            # Too few runs with the same option: every run of the kind is better than nothing.
            chosen = exact if len(exact) >= MIN_SAMPLES_FOR_LINE else same_kind[:HISTORY_SIZE]
            models[(kind, translated)] = fit_duration_model([(row.media_seconds, row.elapsed_seconds) for row in chosen])
    return models


@dataclass(frozen=True)
class QueueInfo:
    # 0: running; 1: next to run; n: n-1 jobs before it.
    position: int
    # Until the end of this job; None without any completed job to learn from.
    seconds_remaining: float | None


def _aware(value: datetime) -> datetime:
    """SQLite returns naive datetimes: they are UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def queue_snapshot(db: Session, now: datetime | None = None) -> dict[str, QueueInfo]:
    """Position and remaining time of every active job, keyed by job id."""
    now = now or datetime.now(timezone.utc)
    rows = db.execute(
        select(ProcessingJob, Video.duration_seconds, Video.target_language)
        .join(Video, Video.id == ProcessingJob.video_id)
        .where(ProcessingJob.status.in_(ACTIVE_JOB_STATUSES))
        .order_by(ProcessingJob.created_at, ProcessingJob.id)
    ).all()
    if not rows:
        return {}
    models = duration_models(db)

    def expected(job: ProcessingJob, media_seconds: float, target_language: str | None) -> float | None:
        model = models.get((job.kind or "FULL", bool(target_language)))
        return model.predict(media_seconds) if model else None

    snapshot: dict[str, QueueInfo] = {}
    # Time before the worker is free; None once an unknown duration is in the way.
    backlog: float | None = 0.0
    for job, media_seconds, target_language in rows:
        if job.status != "RUNNING":
            continue
        total = expected(job, media_seconds, target_language)
        remaining = None
        if total is not None:
            elapsed = (now - _aware(job.started_at)).total_seconds() if job.started_at else 0.0
            # Over the estimate: "almost done" rather than a negative time.
            remaining = max(0.0, total - elapsed)
        snapshot[job.id] = QueueInfo(0, remaining)
        backlog = None if backlog is None or remaining is None else backlog + remaining

    position = 0
    # The worker drains the main queue before the indexing queue (worker_entry).
    queued = [row for row in rows if row[0].status == "QUEUED"]
    queued.sort(key=lambda row: row[0].kind in ("INDEX", "ENTITIES"))
    for job, media_seconds, target_language in queued:
        position += 1
        total = expected(job, media_seconds, target_language)
        backlog = None if backlog is None or total is None else backlog + total
        snapshot[job.id] = QueueInfo(position, backlog)
    return snapshot
