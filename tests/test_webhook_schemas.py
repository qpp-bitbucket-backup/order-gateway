"""Unit tests for webhook and file-upload schemas."""
import pytest
from pydantic import ValidationError

from app.schemas.file_upload import FileUploadUrlsResponse
from app.schemas.webhook import (
    QpmnOrderItemEvent,
    QpmnPackageShippedEvent,
    QpmnShipmentItem,
    QpmnWebhookShipment,
    WebhookResponse,
)
from app.schemas.webhook_registration import (
    WebhookRegistration,
    WebhookRegistrationCreateRequest,
    WebhookRegistrationsListResponse,
    WebhookRegistrationResponse,
    WebhookRegistrationUpdateRequest,
)

_CALLBACK_URL = "https://gw.example.com/api/webhooks/qpmn"


class TestQpmnShipmentItem:
    def test_required_fields(self):
        item = QpmnShipmentItem(itemId="item-1", quantity=2)
        assert item.quantity == 2
        with pytest.raises(ValidationError):
            QpmnShipmentItem(itemId="item-2")


class TestQpmnWebhookShipment:
    def test_all_optional(self):
        shipment = QpmnWebhookShipment()
        assert shipment.id is None
        assert shipment.trackingNumber is None
        assert shipment.items is None

    def test_full_payload(self):
        shipment = QpmnWebhookShipment(
            id=11,
            orderId=22,
            trackingNumber="TRK123",
            trackingUrl="https://t.example.com/TRK123",
            company="DHL",
            shipDate=1700000000000,
            items=[QpmnShipmentItem(itemId="item-3", quantity=1)],
        )
        assert shipment.items[0].itemId == "item-3"


class TestQpmnOrderItemEvent:
    def test_status_required(self):
        with pytest.raises(ValidationError):
            QpmnOrderItemEvent(id="item-4")

    def test_full_payload_with_shipments(self):
        event = QpmnOrderItemEvent(
            id="item-5",
            orderId=33,
            externalId="ext-5",
            unitPrice=9.99,
            storeProductId="sp-1",
            quantity=1,
            status="order_item_produced",
            shipments=[QpmnWebhookShipment(trackingNumber="TRK456")],
        )
        assert event.status == "order_item_produced"
        assert event.shipments[0].trackingNumber == "TRK456"


class TestQpmnPackageShippedEvent:
    def test_inherits_shipment_shape(self):
        event = QpmnPackageShippedEvent(orderId=44, trackingNumber="TRK789")
        assert isinstance(event, QpmnWebhookShipment)
        assert event.orderId == 44


class TestWebhookResponse:
    def test_success_required_message_optional(self):
        resp = WebhookResponse(success=False, message="unmapped status")
        assert resp.message == "unmapped status"
        with pytest.raises(ValidationError):
            WebhookResponse(message="no success field")


class TestWebhookRegistrationCreateRequest:
    def test_valid(self):
        req = WebhookRegistrationCreateRequest(
            name="order-gateway", url=_CALLBACK_URL, eventTypes=["order_item_received"]
        )
        assert req.enabled is True

    def test_event_types_must_not_be_empty(self):
        with pytest.raises(ValidationError):
            WebhookRegistrationCreateRequest(
                name="order-gateway", url=_CALLBACK_URL, eventTypes=[]
            )

    def test_url_required(self):
        with pytest.raises(ValidationError):
            WebhookRegistrationCreateRequest(name="x", eventTypes=["package_shipped"])


class TestWebhookRegistrationUpdateRequest:
    def test_enabled_required_on_update(self):
        req = WebhookRegistrationUpdateRequest(
            name="order-gateway", url=_CALLBACK_URL, eventTypes=["package_shipped"], enabled=False
        )
        assert req.enabled is False
        with pytest.raises(ValidationError):
            WebhookRegistrationUpdateRequest(
                name="order-gateway", url=_CALLBACK_URL, eventTypes=["package_shipped"]
            )


class TestWebhookRegistration:
    """QPMN responses use ``enable``; the model maps it to ``enabled``."""

    def test_accepts_enable_alias(self):
        reg = WebhookRegistration(
            id=1, name="order-gateway", url=_CALLBACK_URL,
            eventTypes=["order_item_received"], enable=True,
        )
        assert reg.enabled is True

    def test_plain_enabled_still_works(self):
        reg = WebhookRegistration(
            id=2, name="order-gateway", url=_CALLBACK_URL,
            eventTypes=["package_shipped"], enabled=False,
        )
        assert reg.enabled is False

    def test_enabled_wins_over_enable(self):
        reg = WebhookRegistration(
            id=3, name="order-gateway", url=_CALLBACK_URL,
            eventTypes=["order_item_audited"], enable=True, enabled=False,
        )
        assert reg.enabled is False


class TestWebhookRegistrationResponses:
    def test_single_response_defaults_success(self):
        reg = WebhookRegistration(
            id=4, name="order-gateway", url=_CALLBACK_URL,
            eventTypes=["order_item_failed"], enabled=True,
        )
        resp = WebhookRegistrationResponse(webhook=reg)
        assert resp.success is True

    def test_list_response_shape(self):
        reg = WebhookRegistration(
            id=5, name="order-gateway", url=_CALLBACK_URL,
            eventTypes=["order_item_canceled"], enabled=True,
        )
        resp = WebhookRegistrationsListResponse(count=1, page=1, pages=1, data=[reg])
        assert resp.data[0].id == 5


class TestFileUploadUrlsResponse:
    def test_fields(self):
        resp = FileUploadUrlsResponse(upload="https://s3/upload", fetch="https://cdn/fetch")
        assert resp.upload.endswith("/upload")
        with pytest.raises(ValidationError):
            FileUploadUrlsResponse(upload="https://s3/upload")
