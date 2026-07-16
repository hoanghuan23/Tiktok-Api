from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class Post(Base):
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True)
    source_id = Column(Integer, ForeignKey("sources.id"), nullable=False)
    tiktok_video_id = Column(String(100), nullable=False, unique=True)
    tiktok_url = Column(String(500), nullable=False)
    description = Column(Text)
    duration_seconds = Column(Integer)
    cover_url = Column(String(500))
    posted_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime)
    is_tracked = Column(Boolean)
    tracking_until = Column(DateTime)
    is_deleted = Column(Boolean)
    last_metric_update = Column(DateTime)
    metric_tier = Column(String(20), nullable=False, default="bootstrap")
    next_metric_update = Column(DateTime)
    cold_check_count = Column(Integer, nullable=False, default=0)
    metric_scan_miss_count = Column(Integer, nullable=False, default=0)

    source = relationship("Source", back_populates="posts")
    metrics = relationship("PostMetric", back_populates="post", order_by="PostMetric.recorded_at")
    comments = relationship("Comment", back_populates="post")
    hashtags = relationship("Hashtag", secondary="post_hashtags", back_populates="posts")
