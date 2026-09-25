"""Quality runs on the reference corpus (n°4) and time per stage (performance tracking)."""
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import desc, select

from .. import ai_models, app_settings, quality, stage_times
from ..db import SessionLocal
from ..main import _stop_rq_job
from ..models import QualityRun, utcnow
from ..schemas import QualityRunIn, QualitySettings

router = APIRouter()


# --- Quality runs on the reference corpus (n°4) ------------------------------------------------------


def _previous_run(db, run: QualityRun) -> QualityRun | None:
    return db.scalar(
        select(QualityRun).where(
            QualityRun.scope == run.scope, QualityRun.status == "COMPLETED", QualityRun.created_at < run.created_at,
        ).order_by(desc(QualityRun.created_at)).limit(1)
    )


@router.get("/performance")
def performance(kind: str = Query("FULL", pattern="^(FULL|SUMMARY|INDEX|ENTITIES|DIARIZE|COMPACT|CLIP)$")):
    """Time per stage over the latest jobs, for one hour of media, and the recent trend."""
    with SessionLocal() as db:
        return stage_times.report(db, kind)


@router.get("/quality")
def quality_overview():
    with SessionLocal() as db:
        runs = list(db.scalars(select(QualityRun).order_by(desc(QualityRun.created_at)).limit(30)))
        config = app_settings.load(db, app_settings.QUALITY, QualitySettings)
        current, version = quality.fingerprint(db)
        tested = db.scalar(select(QualityRun.id).where(QualityRun.fingerprint == current, QualityRun.status == "COMPLETED").limit(1))
        return {
            "items": quality.corpus_items(),
            "available": quality.corpus_available(),
            "settings": config.model_dump(),
            "current": {
                "llm_model": ai_models.llm_model(), "whisper_model": ai_models.whisper_model(), "prompt_version": version,
                # Only a finished run counts: a cancelled or failed one measured nothing.
                "tested": tested is not None,
            },
            "runs": [quality.run_out(run, _previous_run(db, run)) for run in runs],
        }


@router.get("/quality/runs/{run_id}")
def quality_run(run_id: str):
    with SessionLocal() as db:
        run = db.get(QualityRun, run_id)
        if run is None:
            raise HTTPException(404, "Évaluation introuvable")
        return quality.run_out(run, _previous_run(db, run), detail=True)


@router.post("/quality/runs")
def start_quality_run(payload: QualityRunIn):
    if not quality.corpus_available():
        raise HTTPException(409, "Le corpus de référence n'est pas sur cet ordinateur (data/corpus)")
    with SessionLocal() as db:
        if db.scalar(select(QualityRun.id).where(QualityRun.status.in_(quality.ACTIVE)).limit(1)):
            raise HTTPException(409, "Une évaluation est déjà en cours")
    try:
        run = quality.enqueue(payload.scope, "manual")
    except Exception as exc:
        raise HTTPException(503, "Service de traitement indisponible, réessayez ultérieurement") from exc
    return quality.run_out(run)


@router.post("/quality/runs/{run_id}/cancel")
def cancel_quality_run(run_id: str):
    with SessionLocal() as db:
        run = db.get(QualityRun, run_id)
        if run is None:
            raise HTTPException(404, "Évaluation introuvable")
        if run.status not in quality.ACTIVE:
            raise HTTPException(409, "Cette évaluation est déjà terminée")
        running = run.status == "RUNNING"
        run.status, run.current, run.finished_at = "CANCELLED", None, utcnow()
        rq_job_id = run.rq_job_id
        db.commit()
    _stop_rq_job(rq_job_id, running=running)
    return {"cancelled": True}


@router.delete("/quality/runs/{run_id}")
def delete_quality_run(run_id: str):
    with SessionLocal() as db:
        run = db.get(QualityRun, run_id)
        if run is None:
            raise HTTPException(404, "Évaluation introuvable")
        if run.status in quality.ACTIVE:
            raise HTTPException(409, "Annulez l'évaluation en cours avant de la supprimer")
        db.delete(run)
        db.commit()
    return {"deleted": True}


@router.put("/settings/quality", response_model=QualitySettings)
def put_quality_settings(payload: QualitySettings):
    with SessionLocal() as db:
        app_settings.save(db, app_settings.QUALITY, payload)
        db.commit()
    return payload
