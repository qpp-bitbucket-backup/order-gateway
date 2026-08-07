from pydantic import BaseModel, Field, model_validator
from typing import Optional, List


class WebhookShipment(BaseModel):
    """Shipment details carried by a QPMN status webhook."""

    trackingNumber: Optional[str] = Field(None, description="Carrier tracking number")
    carrierName: Optional[str] = Field(None, description="Carrier name")
    service: Optional[str] = Field(None, description="Logistics service type")
    trackingUrl: Optional[str] = Field(None, description="Tracking URL")
    shipDate: Optional[str] = Field(None, description="Ship date (ISO 8601 string)")


class WebhookOrderItemEvent(BaseModel):
    """Item-level status carried inside a QPMN order_updated webhook."""

    id: Optional[str] = Field(None, description="QPMN's own line item ID")
    external_id: Optional[str] = Field(None, description="Our OrderItem.sourceItemId")
    status: str = Field(
        ...,
        description=(
            "QPMN's own item status code (order_item_received/"
            "order_item_reviewed/order_item_produced/package_shipped/"
            "order_item_canceled/order_item_failed) — see EVENT_STATUS_MAP "
            "in app/models/order.py for the mapping to our internal OrderStatus."
        ),
    )


class WebhookOrderEvent(BaseModel):
    """Order payload nested under ``data.order`` in a QPMN order_updated webhook."""

    order_id: Optional[str] = Field(None, description="QPMN's own order ID (maps to our Order.store_order_id)")
    external_id: Optional[str] = Field(None, description="Our Order.source_order_id, echoed back by QPMN")
    created: Optional[int] = Field(None, description="Order creation time (unix epoch seconds)")
    updated: Optional[int] = Field(None, description="Order last-updated time (unix epoch seconds)")
    items: List[WebhookOrderItemEvent] = Field(..., min_length=1, description="Item-level status updates")
    shipments: Optional[List[WebhookShipment]] = Field(
        None, description="Shipment details (present on shipped events)"
    )

    @model_validator(mode="after")
    def _require_order_identifier(self):
        if not self.order_id and not self.external_id:
            raise ValueError("At least one of order_id or external_id must be provided")
        return self


class WebhookOrderUpdatedData(BaseModel):
    """``data`` envelope of a QPMN order_updated webhook."""

    order: WebhookOrderEvent


class QpmnStatusWebhookRequest(BaseModel):
    """QPMN order_updated webhook payload, aligned with Printful's Webhook-API shape.

    Order lookup uses ``data.order.order_id`` (QPMN's own ID, → ``store_order_id``)
    first, falling back to ``data.order.external_id`` (our ``source_order_id``).
    The order-level status transition is driven by the *last* item in
    ``data.order.items`` (multi-item partial-shipment rollup is out of scope for now).
    """

    type: str = Field(..., description="Event type, e.g. 'order_updated'")
    created: int = Field(..., description="Event time (unix epoch seconds)")
    retries: int = Field(0, description="Number of previous delivery attempts for this event")
    store_id: str = Field(..., description="QPMN store ID the event occurred on")
    data: WebhookOrderUpdatedData


class WebhookResponse(BaseModel):
    """Standard webhook acknowledgement response.

    ``success`` reflects whether the status update was applied — both an
    unmapped status code and an invalid state transition report
    ``success: false`` while still returning HTTP 200 (QPMN convention).
    """

    success: bool = Field(..., description="Whether the status update was applied")
    message: Optional[str] = Field(None, description="Optional detail message")
