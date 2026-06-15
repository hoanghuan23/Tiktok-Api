from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class TaskLog(Base):
    __tablename__ = "task_logs"

    id = Column(Integer, primary_key=True)
    task_name = Column(String(100), nullable=False)
    status = Column(String(20))
    started_at = Column(DateTime)
    completed_at = Column(DateTime)
    duration_seconds = Column(Float)
    items_processed = Column(Integer)
    errors_count = Column(Integer)
    error_message = Column(Text)
    created_at = Column(DateTime)


class PipelineJob(Base):
    __tablename__ = "pipeline_jobs"

    id = Column(Integer, primary_key=True)
    job_type = Column(String(20), nullable=False, default="scraper_job")
    source_id = Column(Integer, ForeignKey("sources.id", ondelete="SET NULL"))
    session_id = Column(Integer, ForeignKey("tiktok_sessions.id", ondelete="SET NULL"))
    status = Column(String(10), nullable=False, default="pending")
    posts_found = Column(Integer, nullable=False, default=0)
    posts_new = Column(Integer, nullable=False, default=0)
    items_total = Column(Integer, nullable=False, default=0)
    items_updated = Column(Integer, nullable=False, default=0)
    items_failed = Column(Integer, nullable=False, default=0)
    error_message = Column(Text)
    started_at = Column(DateTime)
    finished_at = Column(DateTime)

    source = relationship("Source", back_populates="jobs")
    session = relationship("TikTokSession", back_populates="jobs")
    logs = relationship("PipelineLog", back_populates="job", order_by="PipelineLog.created_at")
    metrics = relationship("PostMetric", back_populates="job")


class PipelineLog(Base):
    __tablename__ = "pipeline_logs"

    id = Column(Integer, primary_key=True)
    job_id = Column(Integer, ForeignKey("pipeline_jobs.id", ondelete="SET NULL"))
    source_id = Column(Integer, ForeignKey("sources.id"))
    log_level = Column(String(20))
    message = Column(Text, nullable=False)
    error_type = Column(String(100))
    error_details = Column(Text)
    created_at = Column(DateTime)

    job = relationship("PipelineJob", back_populates="logs")
    source = relationship("Source", back_populates="logs")
