"""For a single-item order, one order_item_produced event both advances the
order to PRINTED and (since it's the only/last item) completes it to
PRODUCED in the same request. The "produced" OMS push must never land
before (or be indistinguishable in time from) the "printed" push — see
PRODUCED_NOTIFY_DELAY_SECONDS in app/api/webhooks.py.
"""
import hashlib
import hmac
import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api.webhooks import PRODUCED_NOTIFY_DELAY_SECONDS
from app.core.database import get_session
from app.main import app
from app.models.client import Client
from app.models.order import Order, OrderStatus

STORE_KEY = "test-store-key"
STORE_ORDER_ID = "633900001"
ITEM_ID = "633900002"


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
            order_id="order-single", source_account="test", source_order_id="SRC-SINGLE",
            status=OrderStatus.PRINTREADY, store_id="527017773",
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
            mock_oms.apply_async = MagicMock()
            mock_vfs.delay = MagicMock()
            mock_vfs.apply_async = MagicMock()
            mock_email.delay = MagicMock()
            yield client, session, mock_oms, mock_vfs
        app.dependency_overrides.clear()


def _post_order_item_produced(client: TestClient) -> "object":
    payload = {"id": ITEM_ID, "orderId": int(STORE_ORDER_ID), "status": "produced", "shipments": []}
    body = json.dumps(payload).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "x-qpmn-event-type": "order_item_produced",
        "x-qpmn-event-id": "evt-single-produced",
        "x-qpmn-hmac-sha256": _sign(body),
    }
    return client.post("/api/webhook/qpmn/order-status", content=body, headers=headers)


class TestSingleItemProducedCompletesOrder:
    def test_order_reaches_produced(self, client_session):
        client, session, mock_oms, mock_vfs = client_session

        resp = _post_order_item_produced(client)

        assert resp.status_code == 200
        assert resp.json()["success"] is True

        session.expire_all()
        order = session.exec(select(Order).where(Order.order_id == "order-single")).first()
        assert order.status == OrderStatus.PRODUCED
        assert order.produced_item_ids == [ITEM_ID]

    def test_printed_is_sent_immediately_produced_is_delayed(self, client_session):
        client, session, mock_oms, mock_vfs = client_session

        _post_order_item_produced(client)

        # "printed" — immediate, via .delay(), to both OMS and VFS.
        mock_oms.delay.assert_called_once()
        assert mock_oms.delay.call_args.kwargs["event_status"] == "printed"
        mock_vfs.delay.assert_called_once()
        assert mock_vfs.delay.call_args.kwargs["event_status"] == "printed"

        # "produced" — delayed, via .apply_async(countdown=...), OMS only.
        mock_oms.apply_async.assert_called_once()
        produced_call = mock_oms.apply_async.call_args
        assert produced_call.kwargs["kwargs"]["event_status"] == "produced"
        assert produced_call.kwargs["countdown"] == PRODUCED_NOTIFY_DELAY_SECONDS
        mock_vfs.apply_async.assert_not_called()
