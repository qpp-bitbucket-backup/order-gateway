"""TI-77: an order_item_* webhook's ``id`` must belong to the specified
order. Reproduces the reported bug (a valid orderId + an unrelated item id
could still advance printready -> printed and fire outbound notifications)
and confirms the fix rejects it instead.
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
from app.models.webhook_log import WebhookLog, WebhookProcessStatus

STORE_KEY = "test-store-key"
STORE_ORDER_ID = "527392662"
VALID_ITEM_IDS = ["527392663", "527392665"]


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
            order_id="order-1", source_account="test", source_order_id="SRC-1",
            status=OrderStatus.PRINTREADY, store_id="527017773",
            store_order_id=STORE_ORDER_ID, store_order_item_ids=VALID_ITEM_IDS,
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


def _post_order_item_produced(client: TestClient, item_id: str) -> "object":
    payload = {
        "id": item_id,
        "orderId": int(STORE_ORDER_ID),
        "status": "produced",
        "shipments": [],
    }
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "x-qpmn-event-type": "order_item_produced",
        "x-qpmn-event-id": f"evt-{item_id}",
        "x-qpmn-hmac-sha256": _sign(body),
    }
    return client.post("/api/webhook/qpmn/order-status", content=body, headers=headers)


class TestUnknownItemIdRejected:
    def test_unknown_item_id_does_not_advance_order(self, client_session):
        client, session, mock_oms, mock_vfs = client_session

        resp = _post_order_item_produced(client, "1")  # not one of VALID_ITEM_IDS

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is False
        assert "1" in body["message"]

        session.expire_all()
        order = session.exec(select(Order).where(Order.order_id == "order-1")).first()
        assert order.status == OrderStatus.PRINTREADY, "status must not advance"
        assert not order.produced_item_ids, "the unknown item must not be recorded as produced"

        mock_oms.delay.assert_not_called()
        mock_vfs.delay.assert_not_called()

        log = session.exec(
            select(WebhookLog).where(WebhookLog.event_id == "evt-1")
        ).first()
        assert log.process_status == WebhookProcessStatus.FAILED
        assert "does not belong to order" in log.details

    def test_valid_item_id_still_advances_order(self, client_session):
        """Control case: a real item id for this order behaves as before."""
        client, session, mock_oms, mock_vfs = client_session

        resp = _post_order_item_produced(client, VALID_ITEM_IDS[0])

        assert resp.status_code == 200
        assert resp.json()["success"] is True

        session.expire_all()
        order = session.exec(select(Order).where(Order.order_id == "order-1")).first()
        assert order.status == OrderStatus.PRINTED

        mock_oms.delay.assert_called_once()
        mock_vfs.delay.assert_called_once()
