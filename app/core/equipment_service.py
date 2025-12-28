from sqlalchemy.orm import Session

from app.models.equipment import Equipment


def list_active_equipment(db: Session) -> list[Equipment]:
    """List all active equipment, ordered by category_order, sort_order, then name."""
    return (
        db.query(Equipment)
        .filter(Equipment.is_active.is_(True))
        .order_by(
            Equipment.category_order.asc(),
            Equipment.sort_order.asc(),
            Equipment.name.asc(),
        )
        .all()
    )

