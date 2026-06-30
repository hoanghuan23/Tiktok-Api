from sqlalchemy import Boolean, Column, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.database import Base


class Source(Base):
    __tablename__ = "sources"
    __table_args__ = (UniqueConstraint("source_type", "identifier", name="uq_user_source"),)

    id = Column(Integer, primary_key=True)
    source_type = Column(String(10), nullable=False)
    identifier = Column(String(100), nullable=False)
    display_name = Column(String(255))
    tiktok_url = Column(String(255))
    is_active = Column(Boolean)
    max_days_old = Column(Integer)
    is_accessible = Column(Boolean)
    created_at = Column(DateTime)
    last_scraped = Column(DateTime)
    next_scrape = Column(DateTime)
    schedule_tier = Column(Integer)
    schedule_override_minutes = Column(Integer)

    posts = relationship("Post", back_populates="source")
    analytics = relationship("AnalyticsCache", back_populates="source")
    jobs = relationship("PipelineJob", back_populates="source")
    logs = relationship("PipelineLog", back_populates="source")
