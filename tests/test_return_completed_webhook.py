"""TI-73: the ``order_return_completed`` webhook (§5.4.3/§6.7) should move a
RETURNING order to RETURN_COMPLETED and notify OMS/VFS of the "returncompleted"
status — completing the return loop that was previously left unwired pending
QPMN defining this event.
"""
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.database import get_session
from app.main import app
from app.models.client import Client
from app.models.order import Order, OrderStatus
from app.models.webhook_log import WebhookLog, WebhookDirection, WebhookProcessStatus

STORE_KEY = "test-store-key"
STORE_ORDER_ID = "25874"


def _sign(body: bytes) -> str:
    return hmac.new(STORE_KEY.encode("utf-8"), body, hashlib.sha256).hexdigest()


@pytest.fixture(name="client_session")
def client_session_fixture():
    """TestClient wired to a fresh in-memory DB; Celery notify tasks stubbed."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Client(
            name="test-client", store_id="527017773", store_key=STORE_KEY,
            token="test-token", secret="test-secret",
        ))
        order = Order(
            order_id="order-rt", source_account="test", source_order_id="SRC-RT",
            status=OrderStatus.RETURNING, store_id="527017773",
            store_order_id=STORE_ORDER_ID,
        )
        session.add(order)
        session.commit()

        app.dependency_overrides[get_session] = lambda: session
        with patch("app.main.create_db_and_tables", lambda: None), \
             patch("app.api.webhooks.notify_oms") as mock_oms, \
             patch("app.api.webhooks.notify_vfs") as mock_vfs, \
             patch("app.tasks.notifications.notify_order_status_email") as mock_email, \
             TestClient(app) as client:
            mock_oms.delay = MagicMock()
            mock_vfs.delay = MagicMock()
            mock_email.delay = MagicMock()
            yield client, session, mock_oms, mock_vfs
        app.dependency_overrides.clear()


def _post_return_completed(client: TestClient, event_id: str = "evt-rc-1") -> "object":
    payload = {
        "orderId": int(STORE_ORDER_ID),
        "returnOrder": {
            "id": 123,
            "returnType": "REJECTION",
            "reason": "damaged in transit",
            "items": [{"id": "321", "qty": 1}],
            "returnShipments": [{"trackingNumber": "SF1234567890", "carrierCode": "SF", "carrierName": "顺丰速运"}],
        },
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "x-qpmn-event-type": "order_return_completed",
        "x-qpmn-event-id": event_id,
        "x-qpmn-hmac-sha256": _sign(body),
    }
    return client.post("/api/webhook/qpmn/order-status", content=body, headers=headers)


class TestReturnCompletedWebhook:
    def test_returning_order_moves_to_return_completed(self, client_session):
        client, session, mock_oms, mock_vfs = client_session

        resp = _post_return_completed(client)

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

        session.expire_all()
        order = session.exec(select(Order).where(Order.order_id == "order-rt")).first()
        assert order.status == OrderStatus.RETURN_COMPLETED

        mock_oms.delay.assert_called_once()
        assert mock_oms.delay.call_args.kwargs["event_status"] == "returncompleted"
        mock_vfs.delay.assert_called_once()
        assert mock_vfs.delay.call_args.kwargs["event_status"] == "returncompleted"

        inbound = session.exec(
            select(WebhookLog).where(
                WebhookLog.event_id == "evt-rc-1",
                WebhookLog.direction == WebhookDirection.INBOUND,
            )
        ).first()
        assert inbound.process_status == WebhookProcessStatus.PROCESSED
        assert inbound.order_id == "order-rt"

    def test_order_not_in_returning_is_rejected(self, client_session):
        client, session, mock_oms, mock_vfs = client_session
        order = session.exec(select(Order).where(Order.order_id == "order-rt")).first()
        order.status = OrderStatus.SHIPPED
        session.add(order)
        session.commit()

        resp = _post_return_completed(client, event_id="evt-rc-2")

        assert resp.status_code == 200
        assert resp.json()["success"] is False

        session.expire_all()
        order = session.exec(select(Order).where(Order.order_id == "order-rt")).first()
        assert order.status == OrderStatus.SHIPPED
        mock_oms.delay.assert_not_called()
        mock_vfs.delay.assert_not_called()
