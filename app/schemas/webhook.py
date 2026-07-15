from pydantic import BaseModel, Field, model_validator
from typing import Optional, List


class WebhookShipment(BaseModel):
    """Shipment details carried by a QPMN status webhook."""

    trackingNumber: Optional[str] = Field(None, description="Carrier tracking number")
    carrierName: Optional[str] = Field(None, description="Carrier name")
    shipDate: Optional[str] = Field(None, description="Ship date (ISO 8601 string)")


class QpmnStatusWebhookRequest(BaseModel):
    """QPMN/Popprint order status webhook payload.

    Either ``orderId`` (internal) or ``storeOrderId`` must be present; the
    endpoint looks up the order by ``orderId`` first, falling back to
    ``storeOrderId``.
    """

    orderId: Optional[str] = Field(None, description="Internal order ID")
    storeOrderId: Optional[str] = Field(None, description="Store order ID (fallback lookup)")
    status: str = Field(
        ...,
        description="Order status code (received/dataready/printready/printed/shipped/cancelled/error)",
    )
    timestamp: Optional[str] = Field(None, description="Event timestamp from QPMN")
    shipments: Optional[List[WebhookShipment]] = Field(
        None, description="Shipment details (present on shipped events)"
    )

    @model_validator(mode="after")
    def _require_order_identifier(self):
        if not self.orderId and not self.storeOrderId:
            raise ValueError("At least one of orderId or storeOrderId must be provided")
        return self


class WebhookResponse(BaseModel):
    """Standard webhook acknowledgement response."""

    success: bool = Field(..., description="Whether the webhook was accepted")
    message: Optional[str] = Field(None, description="Optional detail message")
