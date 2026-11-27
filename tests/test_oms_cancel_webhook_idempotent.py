"""When OMS initiates a cancellation (POST /api/oms/order/{source_order_id}/cancel),
order-gateway moves the order to CANCELLED immediately and asks QPMN to
cancel it too. QPMN's own order_item_canceled webhook then arrives after
the fact, for an order that's already CANCELLED — that's a harmless echo,
not an anomaly, so it should be acknowledged (success: true, SKIPPED) rather
than going through the "Invalid transition" path (see app/api/webhooks.py).
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
ITEM_ID = "321"


def _sign(body: bytes) -> str:
    return hmac.new(STORE_KEY.encode("utf-8"), body, hashlib.sha256).hexdigest()


@pytest.fixture(name="client_session")
def client_session_fixture():
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
            order_id="order-cx", source_account="test", source_order_id="SRC-CX",
            status=OrderStatus.CANCELLED, store_id="527017773",
            store_order_id=STORE_ORDER_ID, store_order_item_ids=[ITEM_ID],
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


def _post_order_item_canceled(client: TestClient, event_id: str = "evt-cx-1") -> "object":
    payload = {"id": ITEM_ID, "orderId": int(STORE_ORDER_ID), "status": "order_item_canceled"}
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "x-qpmn-event-type": "order_item_canceled",
        "x-qpmn-event-id": event_id,
        "x-qpmn-hmac-sha256": _sign(body),
    }
    return client.post("/api/webhook/qpmn/order-status", content=body, headers=headers)


class TestCancelEchoIsIgnored:
    def test_cancelled_to_cancelled_is_acknowledged_not_invalid(self, client_session):
        client, session, mock_oms, mock_vfs = client_session

        resp = _post_order_item_canceled(client)

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True

        session.expire_all()
        order = session.exec(select(Order).where(Order.order_id == "order-cx")).first()
        assert order.status == OrderStatus.CANCELLED

        # No re-notification of a status that's already been reported.
        mock_oms.delay.assert_not_called()
        mock_vfs.delay.assert_not_called()

        inbound = session.exec(
            select(WebhookLog).where(
                WebhookLog.event_id == "evt-cx-1",
                WebhookLog.direction == WebhookDirection.INBOUND,
            )
        ).first()
        assert inbound.process_status == WebhookProcessStatus.SKIPPED
        assert inbound.order_id == "order-cx"

    def test_non_cancelled_order_still_rejects_bad_transition(self, client_session):
        client, session, mock_oms, mock_vfs = client_session
        order = session.exec(select(Order).where(Order.order_id == "order-cx")).first()
        order.status = OrderStatus.SHIPPED
        session.add(order)
        session.commit()

        resp = _post_order_item_canceled(client, event_id="evt-cx-2")

        assert resp.status_code == 200
        assert resp.json()["success"] is False

        session.expire_all()
        order = session.exec(select(Order).where(Order.order_id == "order-cx")).first()
        assert order.status == OrderStatus.SHIPPED
        mock_oms.delay.assert_not_called()
        mock_vfs.delay.assert_not_called()
