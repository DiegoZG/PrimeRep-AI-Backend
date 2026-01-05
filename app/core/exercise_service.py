from typing import Optional

from sqlalchemy import delete, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, selectinload

from app.models.exercise import Exercise, exercise_equipment, user_exercise_favorites


MAX_EXERCISE_LIMIT = 200


def list_exercises(
    db: Session,
    *,
    q: Optional[str] = None,
    muscle: Optional[str] = None,
    equipment_id: Optional[str] = None,
    exercise_type: Optional[str] = None,
    only_active: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> list[Exercise]:
    query = db.query(Exercise).options(selectinload(Exercise.equipment))

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


def get_exercise(db: Session, exercise_id: str) -> Optional[Exercise]:
    return (
        db.query(Exercise)
        .options(selectinload(Exercise.equipment))
        .filter(Exercise.id == exercise_id)
        .first()
    )


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
        .order_by(
            Exercise.primary_muscle.asc(),
            Exercise.sort_order.asc(),
            Exercise.name.asc(),
        )
    )
    return query.all()


