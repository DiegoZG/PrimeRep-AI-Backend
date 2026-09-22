from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class EquipmentOut(BaseModel):
    id: str
    name: str
    category: str
    category_order: int
    sort_order: int
    icon_key: Optional[str] = None
    image_url: Optional[str] = None
    is_active: bool
    aliases: list[str] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class EquipmentListOut(BaseModel):
    items: list[EquipmentOut]
    catalog_version: str = "0"
