from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import PipelineJob, Post, Source
from app.schemas.jobs import PipelineJobDetail, PipelineJobRead
from app.services.metric_service import update_post_metric, update_source_metrics
from app.services.scheduler_service import run_scheduler_cycle
from app.services.scraper_service import crawl_source


router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/sources/{source_id}/crawl", response_model=PipelineJobRead)
async def crawl_source_job(
    source_id: int,
    max_count: int = Query(default=30, ge=1, le=200),
    db: Session = Depends(get_db),
) -> PipelineJob:
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    if source.source_type not in {"user", "hashtag", "keyword"}:
        # TODO: ho tro sound crawler.
        raise HTTPException(status_code=400, detail="Chi ho tro crawl user/hashtag/keyword")
    return await crawl_source(db, source, max_count=max_count)


@router.post("/posts/{post_id}/update-metric", response_model=PipelineJobRead)
async def update_metric_job(post_id: int, db: Session = Depends(get_db)) -> PipelineJob:
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return await update_post_metric(db, post)


@router.post("/sources/{source_id}/update-metric", response_model=PipelineJobRead)
async def update_source_metric_job(source_id: int, db: Session = Depends(get_db)) -> PipelineJob:
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return await update_source_metrics(db, source)


@router.post("/scheduler/run")
async def run_scheduler_job(db: Session = Depends(get_db)) -> dict:
    return await run_scheduler_cycle(db)


@router.get("/{job_id}", response_model=PipelineJobDetail)
def get_job(job_id: int, db: Session = Depends(get_db)) -> PipelineJob:
    job = db.get(PipelineJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job
