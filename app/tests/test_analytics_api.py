from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import get_db
from app.main import app
from app.models import AnalyticsCache, Source


def _session():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(bind=engine)
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return session_local()


def test_get_source_analytics_includes_total_bookmarks():
    db = _session()
    source = Source(source_type="hashtag", identifier="python", is_active=True)
    db.add(source)
    db.flush()
    db.add(
        AnalyticsCache(
            source_id=source.id,
            date=datetime(2026, 1, 2),
            total_posts=3,
            total_likes=100,
            total_shares=10,
            total_comments=20,
            total_views=1000,
            total_bookmarks=123,
            avg_likes_per_post=33.33,
            cached_at=datetime(2026, 1, 2, 12, 0, 0),
        )
    )
    db.commit()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    try:
        response = TestClient(app).get(f"/analytics/sources/{source.id}")
    finally:
        app.dependency_overrides.clear()
        db.close()

    assert response.status_code == 200
    assert response.json()[0]["total_bookmarks"] == 123
