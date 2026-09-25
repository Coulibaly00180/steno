"""Summary sources (n°3), actions and decisions (n°5), exports to other tools (n°8)."""
import logging
import uuid
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import desc, func, select

from .. import actions, notes, verification
from ..db import SessionLocal
from ..main import _refuse_if_busy, _video_or_404, search_words
from ..models import ActionItem, Summary, Video, utcnow
from ..schemas import ActionIn
from ..worker import extract_video_actions as worker_extract_actions

logger = logging.getLogger(__name__)
router = APIRouter()
ACTIONS_PAGE_SIZE = 200
ACTIONS_PAGE_MAX = 1000


# --- Summary sources (n°3) --------------------------------------------------------------------

@router.get("/videos/{video_id}/summaries/{summary_id}/sources")
async def summary_sources(video_id: str, summary_id: str):
    """The passage behind each line of the summary; lines without one are to be checked."""

    def compute():
        with SessionLocal() as db:
            summary = db.get(Summary, summary_id)
            if summary is None or summary.video_id != video_id:
                raise HTTPException(404, "Résumé introuvable")
            result = verification.link_sources(db, summary)
            db.commit()
            return result

    try:
        return await run_in_threadpool(compute)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Summary sources unavailable for %s", video_id, exc_info=True)
        raise HTTPException(503, "Sources indisponibles : le modèle d'embeddings ne répond pas") from exc


# --- Actions and decisions (n°5) --------------------------------------------------------------

def _action_out(item: ActionItem, title: str | None = None) -> dict:
    return {
        "id": item.id, "video_id": item.video_id, "video_title": title, "kind": item.kind, "text": item.text, "owner": item.owner,
        "due_text": item.due_text, "due_date": item.due_date, "status": item.status, "start_seconds": item.start_seconds,
        "source": item.source, "edited": item.edited, "created_at": item.created_at, "updated_at": item.updated_at,
    }


def _action_filters(*, video_id: str | None = None, status: str | None = None, kind: str | None = None,
                    owner: str | None = None, q: str | None = None, series_id: str | None = None) -> list:
    conditions = []
    if series_id:
        conditions.append(Video.series_id == series_id)
    if video_id:
        conditions.append(ActionItem.video_id == video_id)
    if status:
        conditions.append(ActionItem.status == status)
    if kind:
        conditions.append(ActionItem.kind == kind)
    if owner:
        conditions.append(func.lower(ActionItem.owner) == owner.strip().lower())
    for word in search_words(q):
        conditions.append(func.lower(ActionItem.text).like(f"%{word}%"))
    return conditions


def _action_rows(db, *, limit: int | None = None, offset: int = 0, **filters) -> list[tuple[ActionItem, str]]:
    """Every matching item (the exports need them all), or one page of them."""
    statement = (
        select(ActionItem, Video.original_filename).join(Video, Video.id == ActionItem.video_id)
        .where(*_action_filters(**filters))
        # Dated first, soonest first; then by video (most recent first) and position.
        .order_by(ActionItem.due_date.is_(None), ActionItem.due_date, desc(Video.created_at), ActionItem.kind,
                  ActionItem.position, ActionItem.id)
        .offset(offset)
    )
    if limit is not None:
        statement = statement.limit(limit)
    return [(item, title) for item, title in db.execute(statement).all()]


def _check_action_filters(status: str | None, kind: str | None) -> None:
    if status and status not in actions.STATUSES:
        raise HTTPException(422, "Statut inconnu")
    if kind and kind not in actions.KINDS:
        raise HTTPException(422, "Type inconnu")


@router.get("/actions")
def list_actions(
    status: str | None = Query(None, max_length=16), kind: str | None = Query(None, max_length=16),
    owner: str | None = Query(None, max_length=80), q: str | None = Query(None, max_length=200),
    series_id: str | None = Query(None, max_length=36),
    limit: int = Query(ACTIONS_PAGE_SIZE, ge=1, le=ACTIONS_PAGE_MAX), offset: int = Query(0, ge=0),
):
    """One page of the actions and decisions of the library, the dated ones first (n°5); `series_id`: one series (n°6).

    `total`: how many match. The exports (CSV, .ics) keep every item.
    """
    _check_action_filters(status, kind)
    filters = {"status": status, "kind": kind, "owner": owner, "q": q, "series_id": series_id}
    with SessionLocal() as db:
        rows = _action_rows(db, limit=limit, offset=offset, **filters)
        total = db.scalar(
            select(func.count(ActionItem.id)).join(Video, Video.id == ActionItem.video_id).where(*_action_filters(**filters))
        )
        owners = sorted(db.scalars(select(ActionItem.owner).where(ActionItem.owner.is_not(None)).distinct()), key=str.casefold)
        return {"items": [_action_out(item, title) for item, title in rows], "owners": owners, "total": total}


@router.get("/videos/{video_id}/actions")
def video_actions(video_id: str):
    with SessionLocal() as db:
        _video_or_404(db, video_id)
        return [_action_out(item, title) for item, title in _action_rows(db, video_id=video_id)]


@router.post("/videos/{video_id}/actions")
def add_action(video_id: str, payload: ActionIn):
    if not payload.text:
        raise HTTPException(422, "Décrivez l'action ou la décision")
    with SessionLocal() as db:
        video = _video_or_404(db, video_id)
        now = utcnow()
        position = (db.scalar(select(func.max(ActionItem.position)).where(ActionItem.video_id == video_id)) or 0) + 1
        item = ActionItem(
            id=str(uuid.uuid4()), video_id=video_id, kind=payload.kind or "action", text=payload.text, owner=payload.owner or None,
            due_text=payload.due_text or None, due_date=payload.due_date, status=payload.status or "open",
            start_seconds=payload.start_seconds, source="manual", edited=True, position=position, created_at=now, updated_at=now,
        )
        db.add(item)
        db.commit()
        return _action_out(item, video.original_filename)


@router.patch("/actions/{action_id}")
def update_action(action_id: str, payload: ActionIn):
    """Only the fields sent change; an edited item is never replaced by a new summary."""
    with SessionLocal() as db:
        item = db.get(ActionItem, action_id)
        if item is None:
            raise HTTPException(404, "Élément introuvable")
        changes = payload.model_dump(exclude_unset=True)
        if "text" in changes and not changes["text"]:
            raise HTTPException(422, "Décrivez l'action ou la décision")
        for field_name, value in changes.items():
            setattr(item, field_name, value if value != "" else None)
        if "owner" in changes or "text" in changes or "due_date" in changes or "due_text" in changes or "kind" in changes:
            item.edited = True
        item.updated_at = utcnow()
        db.commit()
        return _action_out(item)


@router.delete("/actions/{action_id}")
def delete_action(action_id: str):
    with SessionLocal() as db:
        item = db.get(ActionItem, action_id)
        if item is None:
            raise HTTPException(404, "Élément introuvable")
        db.delete(item)
        db.commit()
    return {"deleted": True}


@router.post("/videos/{video_id}/actions/extract")
async def reextract_actions(video_id: str):
    """Read the latest summary again (e.g. after correcting it by hand); edited items are kept."""
    with SessionLocal() as db:
        video = _video_or_404(db, video_id)
        if not video.summaries:
            raise HTTPException(409, "Aucun résumé à lire")
        _refuse_if_busy(db, video_id)
    try:
        added = await run_in_threadpool(worker_extract_actions, video_id)
    except Exception as exc:
        logger.warning("Action extraction failed for %s", video_id, exc_info=True)
        raise HTTPException(503, "Relevé impossible : le modèle de langage ne répond pas") from exc
    return {"added": added}


def _file_response(content: str | bytes, media_type: str, filename: str) -> Response:
    ascii_name = filename.encode("ascii", "ignore").decode() or "export"
    disposition = f'attachment; filename="{ascii_name}"; filename*=UTF-8\'\'{quote(filename)}'
    return Response(content, media_type=media_type, headers={"Content-Disposition": disposition})


@router.get("/actions/export.csv")
def export_actions_csv(status: str | None = Query(None, max_length=16), kind: str | None = Query(None, max_length=16), video_id: str | None = None):
    _check_action_filters(status, kind)
    with SessionLocal() as db:
        rows = _action_rows(db, video_id=video_id, status=status, kind=kind)
    return _file_response(actions.to_csv(rows), "text/csv; charset=utf-8", "actions-steno.csv")


@router.get("/actions/export.ics")
def export_actions_ics(video_id: str | None = None):
    """The open, dated actions as calendar events (Outlook, Google Agenda, Thunderbird)."""
    with SessionLocal() as db:
        rows = _action_rows(db, video_id=video_id, status="open", kind="action")
    return _file_response(actions.to_ics(rows), "text/calendar; charset=utf-8", "echeances-steno.ics")


# --- Exports to other tools (n°8) -------------------------------------------------------------

# Not under /exports/{name}: that route answers first, for the files written by the worker.
@router.get("/videos/{video_id}/note.md")
def export_note(video_id: str, transcript: bool = False):
    """The video as an Obsidian note: front matter, summary, checklist, [[links]] to people."""
    with SessionLocal() as db:
        video = _video_or_404(db, video_id)
        if video.status != "COMPLETED":
            raise HTTPException(404, "Export non disponible")
        content = notes.video_note(db, video, transcript=transcript)
        name = notes.safe_name(notes.video_title(video))
    return _file_response(content, "text/markdown; charset=utf-8", f"{name}.md")


@router.get("/videos/{video_id}/email.eml")
def export_email(video_id: str):
    """A draft e-mail of the report: opens in Outlook or Thunderbird, ready to send."""
    with SessionLocal() as db:
        video = _video_or_404(db, video_id)
        if video.status != "COMPLETED":
            raise HTTPException(404, "Export non disponible")
        content = notes.email_draft(db, video)
        name = notes.safe_name(notes.video_title(video))
    return _file_response(content, "message/rfc822", f"Compte-rendu - {name}.eml")


@router.get("/library/export/obsidian.zip")
def export_obsidian(transcripts: bool = False):
    """The library as an Obsidian folder: a note per video, a page per person, organisation, place, date."""
    return StreamingResponse(
        notes.obsidian_zip(SessionLocal, transcripts=transcripts), media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="steno-obsidian-{datetime.now(timezone.utc):%Y%m%d}.zip"'},
    )
