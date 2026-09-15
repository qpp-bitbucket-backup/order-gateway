from sqlmodel import SQLModel, Field, Column
from sqlalchemy import JSON, Text
from typing import Optional, List, Dict, Any
from enum import Enum
from app.models.base import BaseModel
from app.models.order import OrderStatus
from app.models.webhook_log import WebhookProcessStatus


class NotificationLevel(str, Enum):
    """Severity level of an order status change event.

    Levels tag every status transition written to ``order.logs`` and gate
    per-client email notifications (``clients.notification_config``).
    """

    INFO = "info"          # Normal status flow (received → ... → shipped)
    WARNING = "warning"    # Needs attention (order cancelled)
    ERROR = "error"        # Failed / errored, likely needs human intervention


# OrderStatus -> notification level. Every OrderStatus must appear here —
# status_notification_level() falls back to INFO for unmapped (future)
# statuses, but tests assert full coverage so a new status forces a
# conscious level decision.
STATUS_NOTIFICATION_LEVEL: Dict[OrderStatus, NotificationLevel] = {
    OrderStatus.RECEIVED: NotificationLevel.INFO,
    OrderStatus.PENDING: NotificationLevel.INFO,
    OrderStatus.VALIDATED: NotificationLevel.INFO,
    OrderStatus.COOLING_OFF: NotificationLevel.INFO,
    OrderStatus.PROCESSING: NotificationLevel.INFO,
    OrderStatus.PRINTREADY: NotificationLevel.INFO,
    OrderStatus.PRINTED: NotificationLevel.INFO,
    OrderStatus.PRODUCED: NotificationLevel.INFO,
    OrderStatus.SHIPPED: NotificationLevel.INFO,
    OrderStatus.CANCELLED: NotificationLevel.WARNING,
    OrderStatus.FAILED: NotificationLevel.ERROR,
    OrderStatus.ERRORED: NotificationLevel.ERROR,
}


def status_notification_level(status: OrderStatus) -> NotificationLevel:
    """Level for a status; INFO for anything unmapped (e.g. future statuses)."""
    return STATUS_NOTIFICATION_LEVEL.get(status, NotificationLevel.INFO)


# Statuses that never dispatch an email notification, regardless of the
# client's notification_config: PENDING and PROCESSING are high-frequency
# intermediate states. order.logs still records those transitions (with
# their level) — only the email dispatch is suppressed, in
# enqueue_status_change_email().
EMAIL_MUTED_STATUSES = frozenset({
    OrderStatus.PENDING,
    OrderStatus.PROCESSING,
})


class NotificationEmailLog(BaseModel, table=True):
    """One outbound status-change notification email attempt.

    A row is created (status ``received``) whenever an order changes status
    and the change is recorded via ``record_status_change``; the
    ``notify_order_status_email`` task then resolves the client's per-level
    recipients — marking the row ``skipped`` when that level is disabled or
    unconfigured — renders and sends the email, and marks it ``processed`` or
    retries with exponential backoff (``failed`` once exhausted). Mirrors the
    outbound WebhookLog pattern used for OMS/VFS postbacks.
    """

    __tablename__ = "notification_email_logs"

    order_id: Optional[str] = Field(None, index=True, max_length=64, description="Internal order ID of the transitioned order")
    source_order_id: Optional[str] = Field(None, max_length=64, description="External order ID (VFS sourceOrderId / OMS orderNo)")
    store_id: Optional[str] = Field(None, index=True, max_length=128, description="Store the order belongs to (recipients resolved per store)")
    from_status: Optional[str] = Field(None, max_length=32, description="Order status before the transition")
    to_status: str = Field(nullable=False, index=True, max_length=32, description="Order status after the transition")
    level: str = Field(nullable=False, index=True, max_length=16, description="NotificationLevel of the transition (info/warning/error)")
    message: Optional[str] = Field(None, sa_column=Column(Text), description="Log message attached to the transition, reused for the email body")
    recipients: Optional[List[str]] = Field(None, sa_column=Column(JSON), description="Resolved recipient addresses (filled by the task)")
    process_status: WebhookProcessStatus = Field(default=WebhookProcessStatus.RECEIVED, nullable=False, index=True, description="Processing result")
    details: Optional[str] = Field(None, sa_column=Column(Text), description="Skip reason / send summary / failure detail")
    retry_count: int = Field(default=0, nullable=False, description="Send retry count")
