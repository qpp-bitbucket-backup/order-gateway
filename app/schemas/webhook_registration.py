from typing import Any, List, Optional

from pydantic import BaseModel, Field, model_validator


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
    """A QPMN webhook subscription, as returned by QPMN — not persisted locally.

    Note: QPMN's API uses ``enable`` (no trailing ``d``) in responses, while
    our create/update requests send ``enabled``. We accept both via
    ``model_validator`` and always expose ``enabled``.
    """
    id: int = Field(..., description="QPMN webhook id")
    name: str = Field(..., description="Webhook name")
    url: str = Field(..., description="Callback URL")
    eventTypes: List[str] = Field(..., description="Subscribed event types")
    enabled: bool = Field(..., description="Whether the webhook is enabled")

    @model_validator(mode="before")
    @classmethod
    def _accept_enable_alias(cls, data: Any) -> Any:
        """Map QPMN's ``enable`` field to ``enabled``."""
        if isinstance(data, dict) and "enable" in data and "enabled" not in data:
            data["enabled"] = data.pop("enable")
        return data


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
