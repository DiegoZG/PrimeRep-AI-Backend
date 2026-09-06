from sqlalchemy import Column, DateTime, ForeignKey, String, func
from sqlalchemy.orm import relationship

from app.core.database import Base


class RevokedRefreshToken(Base):
    __tablename__ = "revoked_refresh_tokens"

    jti = Column(String, primary_key=True)
    user_id = Column(String, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    revoked_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)

    user = relationship("User", back_populates="revoked_refresh_tokens")
