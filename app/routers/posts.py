from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Post, PostMetric
from app.schemas.posts import PostDetail, PostRead


router = APIRouter(prefix="/posts", tags=["posts"])


@router.get("", response_model=list[PostRead])
def list_posts(
    source_id: int | None = Query(default=None),
    metric_tier: str | None = Query(default=None),
    is_tracked: bool | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[Post]:
    query = db.query(Post)
    if source_id is not None:
        query = query.filter(Post.source_id == source_id)
    if metric_tier is not None:
        query = query.filter(Post.metric_tier == metric_tier)
    if is_tracked is not None:
        query = query.filter(Post.is_tracked.is_(is_tracked))
    return query.order_by(Post.posted_at.desc()).all()


@router.get("/{post_id}", response_model=PostDetail)
def get_post(post_id: int, db: Session = Depends(get_db)) -> dict:
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    latest_metric = (
        db.query(PostMetric)
        .filter(PostMetric.post_id == post.id)
        .order_by(PostMetric.recorded_at.desc())
        .first()
    )
    return {"post": post, "latest_metric": latest_metric}
