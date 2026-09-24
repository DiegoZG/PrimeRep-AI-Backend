from sqlalchemy import Column, DateTime, ForeignKey, LargeBinary, String, func

from app.core.database import Base


class UserAvatar(Base):
    __tablename__ = "user_avatars"

    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    data = Column(LargeBinary, nullable=False)
    content_type = Column(String, nullable=False, default="image/jpeg")
    version = Column(String, nullable=False)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
