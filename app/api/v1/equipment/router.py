from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.equipment_service import list_active_equipment
from app.schemas.equipment import EquipmentListOut

router = APIRouter()


@router.get("", response_model=EquipmentListOut)
def get_equipment(db: Session = Depends(get_db)):
    """Get all active equipment, ordered by category_order, sort_order, then name."""
    items = list_active_equipment(db)
    return {"items": items}

