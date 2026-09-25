"""Time spent in each stage of a job, and what the history says about it.

The worker timestamps each stage change (`processing_jobs.stage_times`). At the
end of the job the seconds per stage are kept with its duration
(`job_durations.stages`), after the video is deleted too. The page Modèles
shows the median per stage, per hour of media, and how the latest jobs compare
with the earlier ones: a slowdown after a change of model, prompt or driver
shows without running the reference corpus.
"""
import json
from datetime import datetime
from statistics import median

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from .models import JobDuration

# Not a stage of work: the end of a job.
ENDS = frozenset({"COMPLETED", "FAILED", "CANCELLED"})
HISTORY = 50
RECENT = 10
# A median over fewer jobs says nothing.
MIN_JOBS = 3


def append(raw: str | None, stage: str, at: datetime) -> str:
    """The JSON list with `stage` reached at `at` (a repeated stage is not added twice in a row)."""
    try:
        times = json.loads(raw) if raw else []
    except ValueError:
        times = []
    if not times or times[-1][0] != stage:
        times.append([stage, round(at.timestamp(), 3)])
    return json.dumps(times)


def durations(raw: str | None, end: datetime) -> dict[str, float]:
    """Seconds per stage, from the timestamps to `end`; a stage left and reached again adds up."""
    try:
        times = json.loads(raw) if raw else []
    except ValueError:
        return {}
    result: dict[str, float] = {}
    marks = [(stage, float(at)) for stage, at in times] + [("__end__", end.timestamp())]
    for (stage, start), (_, stop) in zip(marks, marks[1:]):
        if stage in ENDS or stop < start:
            continue
        result[stage] = round(result.get(stage, 0.0) + (stop - start), 3)
    return result


def _per_hour(seconds: float, media_seconds: float) -> float | None:
    return seconds * 3600 / media_seconds if media_seconds > 0 else None


def report(db: Session, kind: str = "FULL") -> dict:
    """Median seconds per stage for one hour of media, over the latest jobs, and the recent trend."""
    rows = list(db.scalars(
        select(JobDuration).where(JobDuration.kind == kind, JobDuration.stages.is_not(None))
        .order_by(desc(JobDuration.finished_at)).limit(HISTORY)
    ))
    jobs = []
    for row in rows:
        try:
            stages = json.loads(row.stages or "{}")
        except ValueError:
            continue
        jobs.append((row, stages))
    names: list[str] = []
    for _, stages in jobs:
        for name in stages:
            if name not in names:
                names.append(name)

    def median_per_hour(sample, name):
        values = [_per_hour(stages.get(name, 0.0), row.media_seconds) for row, stages in sample]
        values = [value for value in values if value is not None]
        return round(median(values), 1) if len(values) >= MIN_JOBS else None

    recent, earlier = jobs[:RECENT], jobs[RECENT:]
    stages_out = []
    for name in names:
        present = [(row, stages) for row, stages in jobs if name in stages]
        stages_out.append({
            "stage": name,
            "jobs": len(present),
            "per_media_hour": median_per_hour(present, name),
            "recent": median_per_hour([item for item in recent if name in item[1]], name),
            "earlier": median_per_hour([item for item in earlier if name in item[1]], name),
        })
    totals = [(row, {"total": row.elapsed_seconds}) for row, _ in jobs]
    waits = [row.queued_seconds for row, _ in jobs if row.queued_seconds is not None]
    return {
        "kind": kind,
        "jobs": len(jobs),
        "media_hours": round(sum(row.media_seconds for row, _ in jobs) / 3600, 2),
        "total_per_media_hour": median_per_hour(totals, "total"),
        "total_recent": median_per_hour(totals[:RECENT], "total"),
        "total_earlier": median_per_hour(totals[RECENT:], "total"),
        "queued_median_seconds": round(median(waits), 1) if len(waits) >= MIN_JOBS else None,
        "stages": stages_out,
    }
