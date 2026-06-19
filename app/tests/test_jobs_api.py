import asyncio
from datetime import datetime
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.models import Source
from app.routers import jobs


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return session_local()


def test_update_source_metric_job_returns_pipeline_job(monkeypatch):
    db = _session()
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.commit()
    db.refresh(source)

    async def fake_update_source_metrics(db_arg, source_arg):
        return SimpleNamespace(
            id=77,
            job_type="update_metric",
            source_id=source_arg.id,
            session_id=None,
            status="done",
            posts_found=0,
            posts_new=0,
            items_total=0,
            items_updated=0,
            items_failed=0,
            error_message=None,
            started_at=datetime(2026, 1, 2, 12, 0, 0),
            finished_at=datetime(2026, 1, 2, 12, 0, 1),
        )

    monkeypatch.setattr(jobs, "update_source_metrics", fake_update_source_metrics)
    try:
        response = asyncio.run(jobs.update_source_metric_job(source.id, db))
    finally:
        db.close()

    assert response.id == 77
    assert response.source_id == source.id


def test_update_source_metric_job_route_is_registered():
    paths = {route.path for route in jobs.router.routes}

    assert "/jobs/sources/{source_id}/update-metric" in paths


def test_update_source_metric_job_returns_404_for_missing_source():
    db = _session()
    try:
        try:
            asyncio.run(jobs.update_source_metric_job(999, db))
        except HTTPException as exc:
            assert exc.status_code == 404
            assert exc.detail == "Source not found"
        else:
            raise AssertionError("Expected missing source to raise HTTPException")
    finally:
        db.close()
