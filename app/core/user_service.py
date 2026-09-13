from typing import Optional
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.core.security.emails import normalize_email
from app.models.user import User


def get_user_by_email(db: Session, email: str) -> Optional[User]:
    normalized_email = normalize_email(str(email))
    return db.query(User).filter(func.lower(User.email) == normalized_email).first()


def get_user_by_id(db: Session, user_id: str) -> Optional[User]:
    return db.query(User).filter(User.id == user_id).first()


def delete_user(db: Session, user: User) -> None:
    try:
        db.delete(user)
        db.commit()
    except Exception:
        db.rollback()
        raise


def create_user(
    db: Session,
    *,
    email: str,
    preferred_name: str,
    last_name: Optional[str],
    password_hash: str,
    commit: bool = True,
) -> User:
    user = User(
        email=normalize_email(email),
        preferred_name=preferred_name,
        last_name=last_name,
        password_hash=password_hash,
    )
    db.add(user)
    if commit:
        db.commit()
        db.refresh(user)
    else:
        db.flush()
    return user
