from typing import Optional
from sqlalchemy.orm import Session
from app.models.user import User


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    return db.query(User).filter(User.email == email).first()


def create_user(
    db: Session,
    *,
    email: str,
    preferred_name: str,
    last_name: Optional[str],
    password_hash: str,
) -> User:
    user = User(
        email=email,
        preferred_name=preferred_name,
        last_name=last_name,
        password_hash=password_hash,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user
