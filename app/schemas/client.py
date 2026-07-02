from datetime import datetime,timezone
from typing import List, Optional

from pydantic import BaseModel, Field


class ClientCreateRequest(BaseModel):
    """Request schema for creating a new API client."""
    name: str = Field(..., min_length=1, max_length=128, description="Client display name")
    store_id: str = Field(..., min_length=1, max_length=128, description="Associated store ID")
    store_key: Optional[str] = Field(None, max_length=128, description="Store key for additional identification")
    description: Optional[str] = Field(None, max_length=512, description="Optional description")


class ClientUpdateRequest(BaseModel):
    """Request schema for updating an API client."""
    name: Optional[str] = Field(None, min_length=1, max_length=128, description="Client display name")
    store_id: Optional[str] = Field(None, min_length=1, max_length=128, description="Associated store ID")
    store_key: Optional[str] = Field(None, max_length=128, description="Store key for additional identification")
    description: Optional[str] = Field(None, max_length=512, description="Optional description")
    is_active: Optional[bool] = Field(None, description="Whether the client is active")
    rotate_secret: bool = Field(False, description="Generate a new secret for this client")


class ClientSummary(BaseModel):
    """Client summary without secret."""
    id: int = Field(..., description="Client ID")
    name: str = Field(..., description="Client display name")
    store_id: str = Field(..., description="Associated store ID")
    token: str = Field(..., description="OneFlow API token")
    description: Optional[str] = Field(None, description="Optional description")
    is_active: bool = Field(..., description="Whether the client is active")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")


class ClientCreatedResponse(BaseModel):
    """Response schema when a client is created or secret is rotated."""
    success: bool = Field(True, description="Request success status")
    client: ClientSummary = Field(..., description="Client details")
    secret: str = Field(..., description="Client secret (shown only once)")


class ClientResponse(BaseModel):
    """Response schema for a single client."""
    success: bool = Field(True, description="Request success status")
    client: ClientSummary = Field(..., description="Client details")


class ClientsListResponse(BaseModel):
    """Response schema for paginated client list."""
    success: bool = Field(True, description="Request success status")
    count: int = Field(..., description="Total number of clients")
    page: int = Field(..., description="Current page number")
    pages: int = Field(..., description="Total number of pages")
    data: List[ClientSummary] = Field(..., description="List of clients")
