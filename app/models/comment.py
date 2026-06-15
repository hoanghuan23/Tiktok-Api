from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.database import Base


class Comment(Base):
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True)
    post_id = Column(Integer, ForeignKey("posts.id"), nullable=False)
    parent_id = Column(Integer, ForeignKey("comments.id"))
    tiktok_comment_id = Column(String(100), nullable=False, unique=True)
    commenter_id = Column(String(50))
    commenter_name = Column(String(255))
    comment_text = Column(Text)
    likes_count = Column(Integer)
    reply_count = Column(Integer)
    created_at = Column(DateTime)
    last_updated = Column(DateTime, nullable=False)

    post = relationship("Post", back_populates="comments")
    parent = relationship("Comment", remote_side=[id])
