from sqlmodel import SQLModel, Field
from datetime import datetime,timezone
from typing import Optional


class BaseModel(SQLModel):
    """Base model with common fields for all models."""

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    is_active: bool = Field(default=True, nullable=False)
