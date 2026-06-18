import asyncio
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.models import PipelineLog, Post, Source, TaskLog
from app.services.metric_service import update_post_metric
from app.services.tiktok_client import TikTokClient


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return session_local()


def test_update_post_metric_writes_task_log_summary(monkeypatch):
    async def fake_get_video_info(self, url):
        return {
            "statsV2": {
                "diggCount": "10",
                "shareCount": "2",
                "commentCount": "1",
                "playCount": "100",
                "collectCount": "3",
            }
        }

    monkeypatch.setattr(TikTokClient, "get_video_info", fake_get_video_info)
    db = _session()
    source = Source(source_type="user", identifier="vtv24news", is_active=True)
    db.add(source)
    db.flush()
    post = Post(
        source_id=source.id,
        tiktok_video_id="video-1",
        tiktok_url="https://www.tiktok.com/@vtv24news/video/video-1",
        posted_at=datetime(2026, 1, 2, 11, 0, 0),
    )
    db.add(post)
    db.commit()
    db.refresh(post)

    job = asyncio.run(update_post_metric(db, post))
    task_log = db.query(TaskLog).one()

    assert job.status == "done"
    assert post.metric_tier == "very_low"
    assert post.next_metric_update == post.last_metric_update + timedelta(seconds=200)
    assert task_log.task_name == "update_metrics"
    assert task_log.status == "done"
    assert task_log.items_processed == 1
    assert task_log.errors_count == 0
    assert task_log.error_message is None
    assert db.query(PipelineLog).count() == 0
