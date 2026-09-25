"""Video clips (n°7): cut a passage, list, download, delete."""
import logging
import uuid

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from redis import Redis
from rq import Queue
from sqlalchemy import desc, select

from .. import clips
from ..config import QUEUE_NAME, settings
from ..db import SessionLocal
from ..main import _media_response, _video_or_404, cancel_job
from ..models import ProcessingJob, Video, VideoClip
from ..queue_info import ACTIVE_JOB_STATUSES
from ..schemas import ClipIn
from ..storage import StorageError
from ..utils import timestamp

logger = logging.getLogger(__name__)
router = APIRouter()


# --- Clips (n°7) ---------------------------------------------------------------------------------


def _clip_out(clip: VideoClip, job: ProcessingJob | None) -> dict:
    """The clip, its status taken from its job while it runs (the worker may be stopped before it records the end)."""
    status = clip.status
    if status in ("QUEUED", "RUNNING") and job is not None and job.status in ("FAILED", "CANCELLED"):
        status = job.status
    return {
        "id": clip.id, "video_id": clip.video_id, "title": clip.title,
        "start_seconds": clip.start_seconds, "end_seconds": clip.end_seconds,
        "subtitles": clip.subtitles, "subtitle_source": clip.subtitle_source, "status": status,
        "error": clip.error or (job.error if job is not None and status == "FAILED" else None),
        "progress": job.progress if job is not None and status in ("QUEUED", "RUNNING") else None,
        "job_id": clip.job_id, "filename": clip.filename, "size_bytes": clip.size_bytes, "created_at": clip.created_at,
    }


def _clip_or_404(db, clip_id: str) -> VideoClip:
    clip = db.get(VideoClip, clip_id)
    if clip is None:
        raise HTTPException(404, "Extrait introuvable")
    return clip


def _default_clip_title(video: Video, start: float, end: float) -> str:
    """The chapter the range starts in, else the time range."""
    chapter = next((c for c in reversed(video.chapters) if c.start_seconds <= start + 1), None)
    if chapter is not None and abs(chapter.start_seconds - start) <= 2:
        return chapter.title[:200]
    return f"Extrait {timestamp(start)} – {timestamp(end)}"


@router.get("/videos/{video_id}/clips")
def list_clips(video_id: str):
    with SessionLocal() as db:
        _video_or_404(db, video_id)
        rows = list(db.scalars(select(VideoClip).where(VideoClip.video_id == video_id).order_by(desc(VideoClip.created_at))))
        jobs = {job.id: job for job in db.scalars(select(ProcessingJob).where(
            ProcessingJob.id.in_([clip.job_id for clip in rows if clip.job_id])
        ))}
        return [_clip_out(clip, jobs.get(clip.job_id)) for clip in rows]


@router.post("/videos/{video_id}/clips")
def create_clip(video_id: str, payload: ClipIn):
    """Cut a passage (a CLIP job, main queue): a chapter or a range, subtitles optional (n°7)."""
    with SessionLocal() as db:
        video = _video_or_404(db, video_id)
        if video.status != "COMPLETED":
            raise HTTPException(409, "La vidéo n'est pas encore analysée")
        start = payload.start_seconds
        end = min(payload.end_seconds, video.duration_seconds) if video.duration_seconds else payload.end_seconds
        if end - start < clips.MIN_CLIP_SECONDS:
            raise HTTPException(422, "L'extrait doit durer au moins une seconde, dans la durée de la vidéo")
        if end - start > clips.MAX_CLIP_SECONDS:
            raise HTTPException(422, "Un extrait dure au plus 3 heures")
        try:
            clips.source_media(video)
        except StorageError as exc:
            raise HTTPException(409, str(exc)) from exc
        if payload.subtitle_source == "translation" and not video.translated_text:
            raise HTTPException(409, "Cette vidéo n'a pas de traduction")
        job = ProcessingJob(id=str(uuid.uuid4()), video_id=video_id, kind="CLIP", stage="QUEUED", status="QUEUED", progress=0)
        clip = VideoClip(
            id=str(uuid.uuid4()), video_id=video_id, job_id=job.id,
            title=payload.title or _default_clip_title(video, start, end),
            start_seconds=start, end_seconds=end, subtitles=payload.subtitles, subtitle_source=payload.subtitle_source,
            status="QUEUED",
        )
        db.add_all([job, clip])
        db.commit()
        try:
            queue = Queue(QUEUE_NAME, connection=Redis.from_url(settings.redis_url), default_timeout=21600)
            rq_job = queue.enqueue("app.worker.run_clip", job.id, job_timeout=21600, result_ttl=86400)
        except Exception as exc:
            logger.warning("Unable to enqueue clip job %s: %s", job.id, exc)
            db.delete(clip)
            db.delete(job)
            db.commit()
            raise HTTPException(503, "Service de traitement indisponible, réessayez ultérieurement") from exc
        job.rq_job_id = rq_job.id
        db.commit()
        db.refresh(clip)
        db.refresh(job)
        return _clip_out(clip, job)


@router.get("/clips/{clip_id}/file")
def clip_file(clip_id: str, download: bool = False):
    with SessionLocal() as db:
        clip = _clip_or_404(db, clip_id)
        path = clips.clip_path(clip)
        status = clip.status
    if status != "READY" or path is None or not path.is_file():
        raise HTTPException(404, "Fichier de l'extrait introuvable")
    media_type = "audio/mp4" if path.suffix == ".m4a" else "video/mp4"
    if download:
        return FileResponse(path, media_type=media_type, filename=path.name)
    return _media_response(path)


@router.delete("/clips/{clip_id}")
def delete_clip(clip_id: str):
    """Delete a clip; one being cut is cancelled first."""
    with SessionLocal() as db:
        clip = _clip_or_404(db, clip_id)
        job = db.get(ProcessingJob, clip.job_id) if clip.job_id else None
        active_job = job.id if job is not None and job.status in ACTIVE_JOB_STATUSES else None
    if active_job:
        try:
            cancel_job(active_job)
        except HTTPException:
            pass
    with SessionLocal() as db:
        clip = db.get(VideoClip, clip_id)
        if clip is not None:
            clips.delete_file(clip)
            db.delete(clip)
            db.commit()
    return {"deleted": True}
