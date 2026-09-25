"""Series of meetings (n°6): suggestions, pages, « since the last meeting »."""
import json
import logging
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from .. import series
from ..db import SessionLocal
from ..main import ERROR_CHAT_UNAVAILABLE, _video_or_404
from ..models import ActionItem, MeetingSeries, Video, utcnow
from ..schemas import SeriesIn, SeriesRenameIn, VideoSeriesIn
from .actions import _action_out, _action_rows

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Series of meetings (n°6) ----------------------------------------------------------------------


def _series_or_404(db, series_id: str) -> MeetingSeries:
    found = db.get(MeetingSeries, series_id)
    if found is None:
        raise HTTPException(404, "Série introuvable")
    return found


def _commit_series(db) -> None:
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "Une série porte déjà ce nom") from exc


def _meeting_out(video: Video) -> dict:
    return {
        "id": video.id, "title": series.title_of(video), "status": video.status, "created_at": video.created_at,
        "duration_seconds": video.duration_seconds,
    }


def _assign(db, video_ids: list[str], series_id: str | None) -> int:
    videos = list(db.scalars(select(Video).where(Video.id.in_(video_ids)))) if video_ids else []
    for video in videos:
        if video.series_id != series_id:
            video.series_id = series_id
            video.series_changes = None
    return len(videos)


@router.get("/series")
def list_series():
    with SessionLocal() as db:
        rows = db.execute(
            select(
                MeetingSeries,
                func.count(Video.id),
                func.max(Video.created_at),
            ).outerjoin(Video, Video.series_id == MeetingSeries.id).group_by(MeetingSeries.id).order_by(func.lower(MeetingSeries.name))
        ).all()
        open_counts = dict(db.execute(
            select(Video.series_id, func.count(ActionItem.id)).join(ActionItem, ActionItem.video_id == Video.id)
            .where(Video.series_id.is_not(None), ActionItem.kind == "action", ActionItem.status == "open")
            .group_by(Video.series_id)
        ).all())
        return [
            {"id": found.id, "name": found.name, "meetings": count, "last_meeting_at": last, "open_actions": open_counts.get(found.id, 0)}
            for found, count, last in rows
        ]


@router.get("/series/suggestions")
def series_suggestions():
    """Meetings that look recurring (same title, dates and numbers aside) and belong to no series."""
    with SessionLocal() as db:
        return series.suggestions(db)


@router.post("/series")
def create_series(payload: SeriesIn):
    with SessionLocal() as db:
        found = MeetingSeries(id=str(uuid.uuid4()), name=payload.name)
        db.add(found)
        _commit_series(db)
        _assign(db, payload.video_ids, found.id)
        db.commit()
        return {"id": found.id, "name": found.name}


@router.patch("/series/{series_id}")
def rename_series(series_id: str, payload: SeriesRenameIn):
    with SessionLocal() as db:
        found = _series_or_404(db, series_id)
        found.name = payload.name
        _commit_series(db)
        return {"id": found.id, "name": found.name}


@router.delete("/series/{series_id}")
def delete_series(series_id: str):
    """The series goes; its meetings stay in the library."""
    with SessionLocal() as db:
        found = _series_or_404(db, series_id)
        _assign(db, list(db.scalars(select(Video.id).where(Video.series_id == series_id))), None)
        db.delete(found)
        db.commit()
    return {"deleted": True}


@router.get("/series/{series_id}")
def get_series(series_id: str):
    """The meetings in order, the actions still open across them, and every decision."""
    with SessionLocal() as db:
        found = _series_or_404(db, series_id)
        meetings = series.meetings(db, series_id)
        rows = _action_rows(db, series_id=series_id)
        titles = {video.id: series.title_of(video) for video in meetings}
        order = {video.id: index for index, video in enumerate(meetings)}
        open_actions = [_action_out(item, titles.get(item.video_id)) for item, _ in rows if item.kind == "action" and item.status == "open"]
        decisions = sorted(
            (item for item, _ in rows if item.kind == "decision"), key=lambda item: (order.get(item.video_id, 0), item.position)
        )
        return {
            "id": found.id, "name": found.name, "created_at": found.created_at,
            "meetings": [_meeting_out(video) for video in meetings],
            "open_actions": open_actions,
            "done_actions": sum(1 for item, _ in rows if item.kind == "action" and item.status == "done"),
            "decisions": [_action_out(item, titles.get(item.video_id)) for item in decisions],
        }


@router.get("/videos/{video_id}/series")
def video_series(video_id: str):
    """The meeting's series, its neighbours, and a suggestion when it has none."""
    with SessionLocal() as db:
        video = _video_or_404(db, video_id)
        if video.series_id is None:
            return {"series": None, "previous": None, "next": None, "suggestion": series.suggestion_for(db, video)}
        found = _series_or_404(db, video.series_id)
        meetings = series.meetings(db, found.id)
        index = next((i for i, item in enumerate(meetings) if item.id == video.id), 0)
        return {
            "series": {"id": found.id, "name": found.name, "meetings": len(meetings), "position": index + 1},
            "previous": _meeting_out(meetings[index - 1]) if index > 0 else None,
            "next": _meeting_out(meetings[index + 1]) if index + 1 < len(meetings) else None,
            "suggestion": None,
        }


@router.put("/videos/{video_id}/series")
def set_video_series(video_id: str, payload: VideoSeriesIn):
    with SessionLocal() as db:
        _video_or_404(db, video_id)
        if payload.series_id is not None:
            _series_or_404(db, payload.series_id)
        _assign(db, [video_id], payload.series_id)
        db.commit()
    return video_series(video_id)


def _series_changes(video_id: str, refresh: bool) -> dict:
    """Compare the meeting with the previous one of its series (one LLM call, cached)."""
    with SessionLocal() as db:
        video = _video_or_404(db, video_id)
        if video.series_id is None:
            return {"status": "none"}
        found = _series_or_404(db, video.series_id)
        open_items = series.open_actions_before(db, video)
        titles = {item.video_id: None for item in open_items}
        for row in db.execute(select(Video.id, Video.original_filename).where(Video.id.in_(list(titles)))):
            titles[row.id] = Path(row.original_filename).stem
        open_out = [_action_out(item, titles.get(item.video_id)) for item in open_items]
        previous = series.previous_meeting(db, video)
        if previous is None:
            return {"status": "first", "series": {"id": found.id, "name": found.name}, "open_actions": open_out}
        if not video.summaries:
            return {"status": "no_summary", "series": {"id": found.id, "name": found.name}, "open_actions": open_out}
        key = series.cache_key(previous, video, open_items)
        cached = None if refresh else series.stored_changes(video, key)
        name = found.name
        base = {
            "series": {"id": found.id, "name": found.name},
            "previous": _meeting_out(previous),
            "open_actions": open_out,
        }
        if cached is None:
            # Detached copies for the LLM call: no transaction stays open meanwhile.
            db.expunge_all()
    if cached is None:
        try:
            result = series.compare(name, previous, video, open_items)
        except Exception as exc:
            logger.warning("Series comparison failed for %s", video_id, exc_info=True)
            raise HTTPException(503, ERROR_CHAT_UNAVAILABLE) from exc
        cached = result | {"key": key, "generated_at": utcnow().isoformat()}
        with SessionLocal() as db:
            stored = db.get(Video, video_id)
            if stored is not None:
                stored.series_changes = json.dumps(cached, ensure_ascii=False)
                db.commit()
    evidence = {item["id"]: item["evidence"] for item in cached.get("resolved", [])}
    return base | {
        "status": "ready",
        "new": cached.get("new", []), "changed": cached.get("changed", []), "dropped": cached.get("dropped", []),
        "resolved": [action | {"evidence": evidence[action["id"]]} for action in open_out if action["id"] in evidence],
        "generated_at": cached.get("generated_at"),
    }


@router.get("/videos/{video_id}/series/changes")
async def series_changes(video_id: str, refresh: bool = False):
    return await run_in_threadpool(_series_changes, video_id, refresh)
