from sqlalchemy import Column, DateTime, ForeignKey, Integer
from sqlalchemy.orm import relationship

from app.database import Base


class PostMetric(Base):
    __tablename__ = "post_metrics"

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False)
    likes_count = Column(Integer)
    shares_count = Column(Integer)
    comments_count = Column(Integer)
    views_count = Column(Integer)
    recorded_at = Column(DateTime)
    job_id = Column(Integer, ForeignKey("pipeline_jobs.id", ondelete="SET NULL"))

    post = relationship("Post", back_populates="metrics")
    job = relationship("PipelineJob", back_populates="metrics")
