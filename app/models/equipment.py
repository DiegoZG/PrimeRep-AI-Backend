from sqlalchemy import Column, String, Integer, Boolean, DateTime, func

from app.core.database import Base


class Equipment(Base):
    __tablename__ = "equipment"

    id = Column(String, primary_key=True, nullable=False)
    name = Column(String, nullable=False)
    category = Column(String, nullable=False)
    category_order = Column(Integer, nullable=False, server_default="0")
    sort_order = Column(Integer, nullable=False, server_default="0")
    icon_key = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    is_active = Column(Boolean, nullable=False, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

