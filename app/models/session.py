from sqlalchemy import Boolean, Column, DateTime, Integer, Text
from sqlalchemy.orm import relationship

from app.database import Base


class TikTokSession(Base):
    __tablename__ = "tiktok_sessions"

    id = Column(Integer, primary_key=True)
    sessionid = Column(Text, nullable=False)
    tt_csrf_token = Column(Text, nullable=False)
    ms_token = Column(Text, nullable=False)
    is_active = Column(Boolean, nullable=False)
    is_valid = Column(Boolean, nullable=False)
    last_verified = Column(DateTime)
    expires_at = Column(DateTime)
    created_at = Column(DateTime)

    jobs = relationship("PipelineJob", back_populates="session")

    @property
    def masked_ms_token(self) -> str:
        return self._masked_token(self.ms_token)

    @property
    def masked_sessionid(self) -> str:
        return self._masked_token(self.sessionid)

    @property
    def masked_tt_csrf_token(self) -> str:
        return self._masked_token(self.tt_csrf_token)

    @staticmethod
    def _masked_token(value: str | None) -> str:
        token = value or ""
        if len(token) <= 12:
            return "****"
        return f"{token[:6]}...{token[-6:]}"
