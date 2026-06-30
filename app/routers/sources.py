from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Source
from app.schemas.sources import SourceCreate, SourceRead, SourceUpdate
from app.services.scraper_service import crawl_source_with_videos
from app.services.tiktok_client import TikTokClient


router = APIRouter(prefix="/sources", tags=["sources"])


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _source_url(source_type: str, identifier: str) -> str | None:
    if source_type == "user":
        return f"https://www.tiktok.com/@{identifier}"
    if source_type == "hashtag":
        return f"https://www.tiktok.com/tag/{identifier.lstrip('#')}"
    return None


def _source_identifier(source_type: str, identifier: str) -> str:
    if source_type == "hashtag":
        return identifier.lstrip("#")
    if source_type == "user":
        return identifier.lstrip("@")
    return identifier


@router.post("", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
async def create_source(payload: SourceCreate, db: Session = Depends(get_db)) -> Source:
    # TODO: DB chua co cot include_comments, tam thoi chi nhan request field nay.
    videos = []
    if payload.source_type == "user":
        max_days_old = payload.max_days_old if payload.max_days_old is not None else 1
        since = _now() - timedelta(days=max(max_days_old, 0))
        try:
            identifier, videos = await TikTokClient(db).get_user_profile_videos(
                payload.tiktok_url.strip(),
                max_count=30,
                since=since,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Khong the crawl TikTok URL: {exc}") from exc
        if not identifier:
            raise HTTPException(status_code=400, detail="Khong lay duoc identifier tu yt_dlp uploader")
        tiktok_url = payload.tiktok_url.strip()
    else:
        identifier = _source_identifier(payload.source_type, payload.identifier or "")
        tiktok_url = _source_url(payload.source_type, identifier)

    source = Source(
        source_type=payload.source_type,
        identifier=identifier,
        display_name=payload.display_name,
        tiktok_url=tiktok_url,
        is_active=True,
        max_days_old=payload.max_days_old,
        is_accessible=True,
        created_at=_now(),
    )
    db.add(source)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Source da ton tai") from exc
    db.refresh(source)
    if payload.source_type == "user":
        crawl_source_with_videos(db, source, videos)
        db.refresh(source)
    return source


@router.get("", response_model=list[SourceRead])
def list_sources(
    is_active: bool | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[Source]:
    query = db.query(Source)
    if is_active is not None:
        query = query.filter(Source.is_active.is_(is_active))
    return query.order_by(Source.id.desc()).all()


@router.get("/{source_id}", response_model=SourceRead)
def get_source(source_id: int, db: Session = Depends(get_db)) -> Source:
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    return source


@router.patch("/{source_id}", response_model=SourceRead)
def update_source(source_id: int, payload: SourceUpdate, db: Session = Depends(get_db)) -> Source:
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")

    update_data = payload.model_dump(exclude_unset=True)
    update_data.pop("include_comments", None)
    for field, value in update_data.items():
        setattr(source, field, value)

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Update source khong hop le") from exc
    db.refresh(source)
    return source


@router.delete("/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(source_id: int, db: Session = Depends(get_db)) -> None:
    source = db.get(Source, source_id)
    if not source:
        raise HTTPException(status_code=404, detail="Source not found")
    db.delete(source)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Khong the xoa source dang co du lieu lien quan") from exc
