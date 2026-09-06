from datetime import datetime, timezone
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from app.models.revoked_refresh_token import RevokedRefreshToken


def revoke_refresh_token(db: Session, *, jti: str, user_id: str, expires_at: datetime, commit: bool = True) -> bool:
    statement = insert(RevokedRefreshToken).values(
        jti=jti, user_id=user_id, expires_at=expires_at
    ).on_conflict_do_nothing(index_elements=["jti"])
    result = db.execute(statement)
    if result.rowcount != 1:
        return False
    if commit:
        db.commit()
    return True
