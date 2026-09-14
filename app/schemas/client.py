from datetime import datetime, timezone, date
from typing import List, Optional

from pydantic import BaseModel, Field


class ClientCreateRequest(BaseModel):
    """Request schema for creating a new API client."""
    name: str = Field(..., min_length=1, max_length=128, description="Client display name")
    store_id: str = Field(..., min_length=1, max_length=128, description="Associated store ID")
    store_key: Optional[str] = Field(None, max_length=128, description="Store key for additional identification")
    description: Optional[str] = Field(None, max_length=512, description="Optional description")
    cooling_off_seconds: Optional[int] = Field(None, ge=0, description="Order cooling-off period in seconds before QPMN push (0/omitted = none)")


class PlatformClientCreateRequest(BaseModel):
    """Request schema for creating a new API client via the platform API.

    Unlike the admin-key ``ClientCreateRequest``, every field is required.
    """
    name: str = Field(..., min_length=1, max_length=128, description="Client display name")
    store_id: str = Field(..., min_length=1, max_length=128, description="Associated store ID")
    store_key: str = Field(..., min_length=1, max_length=128, description="Store key for additional identification")
    description: str = Field(..., min_length=1, max_length=512, description="Client description")
    cooling_off_seconds: Optional[int] = Field(None, ge=0, description="Order cooling-off period in seconds before QPMN push (0/omitted = none)")


class ClientSecretRevealRequest(BaseModel):
    """Request schema for revealing a client secret (password confirmation)."""
    store_id: str = Field(..., min_length=1, max_length=128, description="Associated store ID")
    password: str = Field(..., min_length=1, description="Current user's login password (sensitive-operation confirmation)")


class ClientSecretRevealResponse(BaseModel):
    """Response schema for revealing a client secret."""
    success: bool = Field(True, description="Request success status")
    store_id: str = Field(..., description="Store ID the secret belongs to")
    secret: str = Field(..., description="Client secret (sensitive, shown only on demand)")


class ClientSecretUpdateRequest(BaseModel):
    """Request schema for updating a client secret."""
    store_id: str = Field(..., min_length=1, max_length=128, description="Associated store ID")
    secret: str = Field(..., min_length=32, description="New client secret (must be at least 32 characters)")


class ClientStoreKeyRevealRequest(BaseModel):
    """Request schema for revealing a client store key."""
    store_id: str = Field(..., min_length=1, max_length=128, description="Associated store ID")


class ClientStoreKeyRevealResponse(BaseModel):
    """Response schema for revealing a client store key."""
    success: bool = Field(True, description="Request success status")
    store_id: str = Field(..., description="Store ID the store key belongs to")
    store_key: str = Field(..., description="Store key used to authorize against QPMN (sensitive)")


class ClientStoreKeyUpdateRequest(BaseModel):
    """Request schema for updating a client store key."""
    store_id: str = Field(..., min_length=1, max_length=128, description="Associated store ID")
    store_key: str = Field(..., min_length=1, max_length=128, description="New store key (verified against QPMN before saving)")


class ClientUpdateRequest(BaseModel):
    """Request schema for updating an API client."""
    name: Optional[str] = Field(None, min_length=1, max_length=128, description="Client display name")
    store_id: Optional[str] = Field(None, min_length=1, max_length=128, description="Associated store ID")
    store_key: Optional[str] = Field(None, max_length=128, description="Store key for additional identification")
    description: Optional[str] = Field(None, max_length=512, description="Optional description")
    cooling_off_seconds: Optional[int] = Field(None, ge=0, description="Order cooling-off period in seconds before QPMN push (0 = none)")
    is_active: Optional[bool] = Field(None, description="Whether the client is active")
    rotate_secret: bool = Field(False, description="Generate a new secret for this client")


class ClientSummary(BaseModel):
    """Client summary without secret."""
    id: int = Field(..., description="Client ID")
    name: str = Field(..., description="Client display name")
    store_id: str = Field(..., description="Associated store ID")
    token: str = Field(..., description="OneFlow API token")
    description: Optional[str] = Field(None, description="Optional description")
    cooling_off_seconds: int = Field(0, description="Order cooling-off period in seconds before QPMN push (0 = none)")
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


class PlatformSalesStatSummary(BaseModel):
    """One store's sales figures for a single day (stats row granularity)."""
    statDate: date = Field(..., description="UTC day the figures cover")
    clientId: int = Field(..., description="Client ID the stats belong to")
    storeName: str = Field(..., description="Client (store) display name")
    storeId: str = Field(..., description="Store ID")
    currency: Optional[str] = Field(None, description="Store currency code; totalAmount is in this currency")
    ordersRequested: int = Field(..., description="Order requests for the day")
    ordersSubmitted: int = Field(..., description="Orders successfully submitted to QPMN for the day")
    lineItemsCount: int = Field(..., description="Line items for the day")
    lineItemsQuantity: int = Field(..., description="Item quantity for the day")
    totalAmount: float = Field(..., description="Sales amount for the day")


class PlatformSalesStatsResponse(BaseModel):
    """Response schema for the platform sales statistics report."""
    success: bool = Field(True, description="Request success status")
    count: int = Field(..., description="Number of stat rows returned")
    startDate: date = Field(..., description="Range start actually applied (inclusive)")
    endDate: date = Field(..., description="Range end actually applied (inclusive)")
    data: List[PlatformSalesStatSummary] = Field(..., description="Per-store daily figures, ordered by statDate (then storeName)")
