"""Order cancel list model: OMS cancel requests that arrived before the order did."""
from enum import Enum
from typing import Optional

from sqlmodel import Field

from app.models.base import BaseModel


class OrderCancelRecordStatus(str, Enum):
    """Lifecycle of an order_cancel_list row."""

    PENDING = "pending"      # waiting for a matching order to arrive
    PROCESSED = "processed"  # the matching order was cancelled by the publishing worker


class OrderCancelRecord(BaseModel, table=True):
    """An OMS cancel request whose sourceOrderId matched no active order yet.

    Written on the OMS cancel endpoint's no-order path (acknowledged to OMS
    as an accepted deferred cancellation); the order_publishing worker checks
    for a pending record when a VFS-created order enters the pipeline and
    cancels it locally instead of publishing — the order has not been pushed
    to QPMN yet, so no QPMN cancel call is needed.
    """

    __tablename__ = "order_cancel_list"

    source_order_id: str = Field(
        index=True,
        nullable=False,
        max_length=64,
        description="sourceOrderId carried by the OMS cancel request",
    )
    status: OrderCancelRecordStatus = Field(
        default=OrderCancelRecordStatus.PENDING,
        nullable=False,
        index=True,
        description=(
            "pending = waiting for the order to arrive; "
            "processed = the matching order was cancelled by the publishing worker"
        ),
    )
    reason: Optional[str] = Field(
        None,
        max_length=512,
        description="Cancel reason from the OMS request, if any",
    )
    order_id: Optional[str] = Field(
        None,
        index=True,
        max_length=64,
        description="Internal order id of the order cancelled via this record (filled when processed)",
    )
    processed_message: Optional[str] = Field(
        None,
        max_length=512,
        description="Outcome stamped when the record was processed",
    )
