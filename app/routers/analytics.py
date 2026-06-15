from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import AnalyticsCache, Source
from app.schemas.analytics import AnalyticsCacheRead


router = APIRouter(prefix="/analytics", tags=["analytics"])


@router.get("/sources/{source_id}", response_model=list[AnalyticsCacheRead])
def get_source_analytics(source_id: int, db: Session = Depends(get_db)) -> list[AnalyticsCache]:
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return (
        db.query(AnalyticsCache)
        .filter(AnalyticsCache.source_id == source_id)
        .order_by(AnalyticsCache.date.desc())
        .all()
    )
