from typing import Optional

from pydantic import BaseModel, ConfigDict


class EquipmentOut(BaseModel):
    id: str
    name: str
    category: str
    category_order: int
    sort_order: int
    icon_key: Optional[str] = None
    image_url: Optional[str] = None
    is_active: bool

    model_config = ConfigDict(from_attributes=True)


class EquipmentListOut(BaseModel):
    items: list[EquipmentOut]

