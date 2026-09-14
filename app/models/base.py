from sqlmodel import SQLModel, Field
from datetime import datetime,timezone
from typing import Optional


class BaseModel(SQLModel):
    """Base model with common fields for all models."""

    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), nullable=False)
    is_active: bool = Field(default=True, nullable=False)
