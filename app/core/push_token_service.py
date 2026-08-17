from datetime import datetime, timezone

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.models.push_token import PushToken


def register_push_token(
    db: Session, *, user_id: str, token: str, platform: str
) -> PushToken:
    statement = insert(PushToken).values(
        token=token,
        user_id=user_id,
        platform=platform,
        updated_at=datetime.now(timezone.utc),
    )
    statement = statement.on_conflict_do_update(
        index_elements=[PushToken.token],
        set_={
            "user_id": statement.excluded.user_id,
            "platform": statement.excluded.platform,
            "updated_at": statement.excluded.updated_at,
        },
    )
    db.execute(statement)
    db.commit()
    return db.get(PushToken, token)


def unregister_push_token(db: Session, *, user_id: str, token: str) -> None:
    db.query(PushToken).filter(
        PushToken.token == token,
        PushToken.user_id == user_id,
    ).delete(synchronize_session=False)
    db.commit()
