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
    store_order_id: Optional[str] = Field(None, index=True, max_length=64, description="Store order ID (QPMN orderId)")
    store_item_id: Optional[str] = Field(None, index=True, max_length=64, description="Store order item ID (QPMN order_item id), set for order_item_* events only")
    event_status: Optional[str] = Field(None, index=True, max_length=32, description="Order status carried by the event")
    payload: Optional[Dict[str, Any]] = Field(None, sa_column=Column(JSON), description="Raw request/response body")
    process_status: WebhookProcessStatus = Field(default=WebhookProcessStatus.RECEIVED, nullable=False, index=True, description="Processing result")
    details: Optional[Any] = Field(None, sa_column=Column(JSON), description="Failure reason when FAILED; the response sent to QPMN (inbound) or received from OMS/VFS (outbound) otherwise")
    retry_count: int = Field(default=0, nullable=False, description="Delivery retry count (outbound)")
