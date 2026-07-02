from sqlmodel import SQLModel, Field
from typing import Optional
from datetime import datetime,timezone
from app.models.base import BaseModel


class FileUpload(BaseModel, table=True):
    """File upload tracking model."""

    __tablename__ = "file_uploads"

    file_id: str = Field(unique=True, index=True, nullable=False, description="Unique file identifier")
    mime_type: str = Field(nullable=False, description="MIME type of the file")
    upload_url: str = Field(nullable=False, description="Pre-signed upload URL")
    fetch_url: str = Field(nullable=False, description="URL for fetching the file in orders")
    uploaded_at: Optional[datetime] = Field(None, description="When file was uploaded")
    file_size: Optional[int] = Field(None, ge=0, description="File size in bytes")
    status: str = Field(default="pending", description="Upload status: pending, uploaded, failed")
    store_id: Optional[str] = Field(None, index=True, description="Store identifier")
