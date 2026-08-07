from pydantic import BaseModel, Field
from typing import Optional, List


class QpmnShipmentItem(BaseModel):
    """Per-item quantity within a shipment (QPMN webhook spec §5.4.2)."""

    itemId: str = Field(..., description="Store retail order item id")
    quantity: int = Field(..., description="Quantity shipped")


class QpmnWebhookShipment(BaseModel):
    """Shipment object — shared shape for ``order_item_*``'s embedded
    ``shipments[]`` (§5.4.1) and the standalone ``package_shipped`` event
    body (§5.4.2)."""

    id: Optional[int] = Field(None, description="Shipment id")
    orderId: Optional[int] = Field(None, description="Store retail order id (QPMN's own order id)")
    trackingNumber: Optional[str] = Field(None, description="Carrier tracking number")
    trackingUrl: Optional[str] = Field(None, description="Tracking URL")
    company: Optional[str] = Field(None, description="Shipping carrier")
    shipDate: Optional[int] = Field(None, description="Ship date, epoch milliseconds")
    items: Optional[List[QpmnShipmentItem]] = Field(None, description="Items included in this shipment")


class QpmnOrderItemEvent(BaseModel):
    """Body for ``order_item_*`` events (QPMN webhook spec §5.4.1).

    NOTE: ``orderId`` isn't in QPMN's documented spec for this event type
    (only ``package_shipped``'s payload has it) — assumed here pending QPMN
    confirming they'll add it (see docs/order-gateway-oms-todo.md #3).
    """

    id: str = Field(..., description="Store retail order item id")
    orderId: Optional[int] = Field(None, description="Store retail order id — ASSUMED, unconfirmed by QPMN")
    externalId: Optional[str] = Field(None, description="Our OrderItem.sourceItemId")
    unitPrice: Optional[float] = Field(None, description="Unit price")
    storeProductId: Optional[str] = Field(None, description="Store product id")
    quantity: Optional[int] = Field(None, description="Quantity")
    status: str = Field(
        ...,
        description=(
            "QPMN's item status code (order_item_received/order_item_reviewed/"
            "order_item_produced/order_item_canceled/order_item_failed) — see "
            "EVENT_STATUS_MAP in app/models/order.py for the mapping to our "
            "internal OrderStatus."
        ),
    )
    shipments: Optional[List[QpmnWebhookShipment]] = Field(None, description="Shipments, empty until shipped")


class QpmnPackageShippedEvent(QpmnWebhookShipment):
    """Body for ``package_shipped`` events (§5.4.2) — identical shape to ``QpmnWebhookShipment``."""


class WebhookResponse(BaseModel):
    """Standard webhook acknowledgement response.

    ``success`` reflects whether the status update was applied — both an
    unmapped status code and an invalid state transition report
    ``success: false`` while still returning HTTP 200 (QPMN convention).
    """

    success: bool = Field(..., description="Whether the status update was applied")
    message: Optional[str] = Field(None, description="Optional detail message")
