from sqlmodel import SQLModel, Field
from typing import Optional
from app.models.base import BaseModel


class AddressMapping(BaseModel, table=True):
    """Province/state code mapping (seeded from docs/PS.CN运费.xlsx)."""

    __tablename__ = "address_mapping"

    state_code: str = Field(unique=True, index=True, max_length=12, description="Administrative division code (e.g. 330000)")
    state_desc: str = Field(unique=True, index=True, max_length=64, description="Province name in Chinese (e.g. 浙江省)")
    state_name_en: Optional[str] = Field(None, index=True, max_length=64, description="Province name in English (e.g. Zhejiang)")
