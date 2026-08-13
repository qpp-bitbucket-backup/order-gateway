from typing import List, Optional

from pydantic import BaseModel, Field


class WebhookRegistrationCreateRequest(BaseModel):
    """Request schema for registering a QPMN webhook (§4.1)."""
    name: str = Field(..., description="Webhook name")
    url: str = Field(..., description="Callback URL QPMN will POST events to")
    eventTypes: List[str] = Field(..., min_length=1, description="Subscribed event types")
    enabled: bool = Field(True, description="Whether the webhook is enabled")


class WebhookRegistrationUpdateRequest(BaseModel):
    """Request schema for updating a QPMN webhook (§4.4) — QPMN treats this as a full replace."""
    name: str = Field(..., description="Webhook name")
    url: str = Field(..., description="Callback URL QPMN will POST events to")
    eventTypes: List[str] = Field(..., min_length=1, description="Subscribed event types")
    enabled: bool = Field(..., description="Whether the webhook is enabled")


class WebhookRegistration(BaseModel):
    """A QPMN webhook subscription, as returned by QPMN — not persisted locally."""
    id: int = Field(..., description="QPMN webhook id")
    name: str = Field(..., description="Webhook name")
    url: str = Field(..., description="Callback URL")
    eventTypes: List[str] = Field(..., description="Subscribed event types")
    enabled: bool = Field(..., description="Whether the webhook is enabled")


class WebhookRegistrationResponse(BaseModel):
    """Response schema for a single webhook."""
    success: bool = Field(True, description="Request success status")
    webhook: WebhookRegistration = Field(..., description="Webhook details")


class WebhookRegistrationsListResponse(BaseModel):
    """Response schema for paginated webhook list."""
    success: bool = Field(True, description="Request success status")
    count: int = Field(..., description="Total number of webhooks")
    page: int = Field(..., description="Current page number")
    pages: int = Field(..., description="Total number of pages")
    data: List[WebhookRegistration] = Field(..., description="List of webhooks")
