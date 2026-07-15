from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON
from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum
from app.models.base import BaseModel


class WebhookDirection(str, Enum):
    """Direction of the webhook notification."""
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class WebhookProcessStatus(str, Enum):
    """Processing status of a webhook log entry."""
    RECEIVED = "received"
    PROCESSED = "processed"
    SKIPPED = "skipped"
    FAILED = "failed"


class WebhookLog(BaseModel, table=True):
    """Log of inbound/outbound order status webhook notifications."""

    __tablename__ = "webhook_logs"

    direction: WebhookDirection = Field(nullable=False, index=True, description="inbound (QPMN -> gateway) or outbound (gateway -> OMS/VFS)")
    source: str = Field(nullable=False, index=True, max_length=20, description="Counterparty system: qpmn / oms / vfs")
    order_id: Optional[str] = Field(None, index=True, max_length=64, description="Internal order ID")
    source_order_id: Optional[str] = Field(None, index=True, max_length=64, description="External order ID (VFS sourceOrderId / OMS orderNo)")
    event_status: Optional[str] = Field(None, index=True, max_length=32, description="Order status carried by the event")
    payload: Optional[Dict[str, Any]] = Field(None, sa_column=Column(JSON), description="Raw request/response body")
    headers: Optional[Dict[str, Any]] = Field(None, sa_column=Column(JSON), description="Request headers including signature")
    signature_valid: Optional[bool] = Field(None, description="Inbound signature/token verification result")
    process_status: WebhookProcessStatus = Field(default=WebhookProcessStatus.RECEIVED, nullable=False, index=True, description="Processing result")
    error_message: Optional[str] = Field(None, max_length=512, description="Failure reason")
    retry_count: int = Field(default=0, nullable=False, description="Delivery retry count (outbound)")
    processed_at: Optional[datetime] = Field(None, description="When processing finished")
