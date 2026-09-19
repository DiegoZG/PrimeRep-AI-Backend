from typing import Optional

from sqlalchemy import and_, delete, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, selectinload

from app.models.exercise import Exercise, exercise_equipment, user_exercise_favorites


MAX_EXERCISE_LIMIT = 200


def exercise_visibility_filter(user_id: Optional[str]):
    """Seed exercises are shared; user-created exercises are private to their owner."""
    seed_visibility = Exercise.source != "user"
    if user_id is None:
        return seed_visibility
    return or_(seed_visibility, and_(Exercise.source == "user", Exercise.owner_user_id == user_id))


def list_exercises(
    db: Session,
    *,
    q: Optional[str] = None,
    muscle: Optional[str] = None,
    equipment_id: Optional[str] = None,
    exercise_type: Optional[str] = None,
    only_active: bool = True,
    user_id: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Exercise]:
    query = db.query(Exercise).options(selectinload(Exercise.equipment))
    query = query.filter(exercise_visibility_filter(user_id))

    if only_active:
        query = query.filter(Exercise.is_active.is_(True))

    if q:
        like = f"%{q}%"
        query = query.filter(
            or_(Exercise.name.ilike(like), Exercise.id.ilike(like)),
        )

    if muscle:
        query = query.filter(
            or_(
                Exercise.primary_muscle == muscle,
                Exercise.secondary_muscles.contains([muscle]),
            )
        )

    if exercise_type:
        query = query.filter(Exercise.exercise_type == exercise_type)

    if equipment_id:
        query = query.join(
            exercise_equipment,
            exercise_equipment.c.exercise_id == Exercise.id,
        ).filter(exercise_equipment.c.equipment_id == equipment_id)

    capped_limit = min(limit, MAX_EXERCISE_LIMIT)

    query = query.order_by(
        Exercise.primary_muscle.asc(),
        Exercise.sort_order.asc(),
        Exercise.name.asc(),
    ).offset(offset or 0)

    if capped_limit > 0:
        query = query.limit(capped_limit)

    return query.all()


def get_exercise(
    db: Session, exercise_id: str, *, user_id: Optional[str] = None, include_inactive: bool = False
) -> Optional[Exercise]:
    query = (
        db.query(Exercise)
        .options(selectinload(Exercise.equipment))
        .filter(Exercise.id == exercise_id, exercise_visibility_filter(user_id))
    )
    if not include_inactive:
        query = query.filter(Exercise.is_active.is_(True))
    return query.first()


def is_favorited(db: Session, user_id: str, exercise_id: str) -> bool:
    row = (
        db.query(user_exercise_favorites)
        .filter(
            user_exercise_favorites.c.user_id == user_id,
            user_exercise_favorites.c.exercise_id == exercise_id,
        )
        .first()
    )
    return row is not None


def list_favorite_ids(
    db: Session, user_id: str, exercise_ids: list[str]
) -> set[str]:
    if not exercise_ids:
        return set()

    rows = (
        db.query(user_exercise_favorites.c.exercise_id)
        .filter(
            user_exercise_favorites.c.user_id == user_id,
            user_exercise_favorites.c.exercise_id.in_(exercise_ids),
        )
        .all()
    )
    return {row.exercise_id for row in rows}


def favorite_exercise(db: Session, user_id: str, exercise_id: str) -> None:
    stmt = (
        pg_insert(user_exercise_favorites)
        .values(user_id=user_id, exercise_id=exercise_id)
        .on_conflict_do_nothing(
            index_elements=[
                user_exercise_favorites.c.user_id,
                user_exercise_favorites.c.exercise_id,
            ]
        )
    )
    db.execute(stmt)
    db.commit()


def unfavorite_exercise(db: Session, user_id: str, exercise_id: str) -> None:
    stmt = delete(user_exercise_favorites).where(
        user_exercise_favorites.c.user_id == user_id,
        user_exercise_favorites.c.exercise_id == exercise_id,
    )
    db.execute(stmt)
    db.commit()


def list_favorites(db: Session, user_id: str) -> list[Exercise]:
    query = (
        db.query(Exercise)
        .options(selectinload(Exercise.equipment))
        .join(
            user_exercise_favorites,
            user_exercise_favorites.c.exercise_id == Exercise.id,
        )
        .filter(user_exercise_favorites.c.user_id == user_id)
        .filter(Exercise.is_active.is_(True), exercise_visibility_filter(user_id))
        .order_by(
            Exercise.primary_muscle.asc(),
            Exercise.sort_order.asc(),
            Exercise.name.asc(),
        )
    )
    return query.all()


def exercise_to_dict(exercise: Exercise, *, user_id: Optional[str], favorited: bool = False) -> dict:
    return {
        "id": exercise.id,
        "name": exercise.name,
        "exercise_type": exercise.exercise_type,
        "primary_muscle": exercise.primary_muscle,
        "secondary_muscles": exercise.secondary_muscles or [],
        "required_equipment_ids": [item.id for item in exercise.equipment],
        "demo_video_url": exercise.demo_video_url,
        "image_url": exercise.image_url,
        "is_active": exercise.is_active,
        "is_favorited": favorited,
        "source": "custom" if exercise.source == "user" else exercise.source,
        "is_editable": bool(user_id and exercise.source == "user" and exercise.owner_user_id == user_id),
        "how_to": exercise.how_to,
        "why_it_works": exercise.why_it_works,
        "common_mistakes": exercise.common_mistakes,
        "beginner_notes": exercise.beginner_notes,
    }
