from sqlalchemy import Column, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.database import Base


class PostHashtag(Base):
    __tablename__ = "post_hashtags"

    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True)
    hashtag_id = Column(Integer, ForeignKey("hashtags.id", ondelete="CASCADE"), primary_key=True)


class Hashtag(Base):
    __tablename__ = "hashtags"

    id = Column(Integer, primary_key=True)
    tag = Column(String(100), nullable=False, unique=True, index=True)

    posts = relationship("Post", secondary="post_hashtags", back_populates="hashtags")
