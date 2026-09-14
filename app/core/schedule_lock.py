from sqlalchemy.orm import Session

from app.models.user import User


def lock_user_schedule(db: Session, user_id: str) -> None:
    """Serialize schedule read-modify-write operations for one user."""
    user = (
        db.query(User.id)
        .filter(User.id == user_id)
        .with_for_update()
        .one_or_none()
    )
    if user is None:
        raise ValueError("User not found")
