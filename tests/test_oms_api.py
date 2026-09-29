"""Unit tests for the OMS cancel / return APIs (app/api/oms.py).

TestClient wired to a fresh in-memory DB with the OMS auth dependency
stubbed — the endpoint logic under test is the sourceOrderId lookup
branches, idempotency, error mapping and the inbound webhook_logs audit;
the QPMN-facing service calls are patched at the oms_api import site.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.api import oms as oms_api
from app.core.auth_oms import verify_oms_auth
from app.core.database import get_session
from app.main import app
from app.models.order import Order, OrderStatus
from app.models.webhook_log import WebhookDirection, WebhookLog, WebhookProcessStatus
from app.services.order import (
    OrderNotCancellableError,
    OrderNotReturnableError,
    QPMNCancelError,
    QPMNReturnError,
)


@pytest.fixture(name="client_session")
def client_session_fixture():
    """TestClient wired to a fresh in-memory DB, OMS auth stubbed.

    StaticPool + check_same_thread=False keeps every thread (the TestClient
    portal thread included) on the same in-memory connection, so the tables
    created here are visible to request handlers.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[verify_oms_auth] = lambda: "test-oms-key"
        # The lifespan's create_db_and_tables() targets the real MySQL
        # engine; the tables already exist on the in-memory engine above, so
        # stub it out — keeps the whole test DB-free for CI runners.
        with patch("app.main.create_db_and_tables", lambda: None), TestClient(app) as client:
            yield client, session
        app.dependency_overrides.clear()


def _add_order(
    session,
    *,
    order_id,
    source_order_id,
    status,
    store_id="store-1",
    active=True,
):
    order = Order(
        order_id=order_id,
        source_account="oms-test-account",
        source_order_id=source_order_id,
        status=status,
        store_id=store_id,
        store_order_id=f"qpmn-{order_id}",
        is_active=active,
    )
    session.add(order)
    session.commit()
    return order


def _oms_audit_logs(session):
    return list(
        session.exec(select(WebhookLog).where(WebhookLog.source == "OMS")).all()
    )


RETURN_BODY = {
    "reason": "damaged in transit",
    "trackingNumber": "1Z999AA10123456784",
    "carrierName": "FedEx",
}


# ---------------------------------------------------------------------------
# POST /api/oms/order/{source_order_id}/cancel
# ---------------------------------------------------------------------------

class TestOMSCancelOrder:
    def test_missing_order_is_deferred(self, client_session):
        client, session = client_session
        resp = client.post("/api/oms/order/NOPE-1/cancel", json={"reason": "test"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["order_id"] is None
        assert "recorded" in body["message"]
        # The accepted deferred cancellation is still audited (as processed).
        logs = _oms_audit_logs(session)
        assert len(logs) == 1
        assert logs[0].direction == WebhookDirection.INBOUND
        assert logs[0].process_status == WebhookProcessStatus.PROCESSED

    def test_ambiguous_source_order_id_returns_candidates(self, client_session):
        client, session = client_session
        _add_order(session, order_id="ord-a", source_order_id="DUP-1",
                   status=OrderStatus.PENDING, store_id="store-1")
        _add_order(session, order_id="ord-b", source_order_id="DUP-1",
                   status=OrderStatus.SHIPPED, store_id="store-2")

        with patch.object(oms_api.order_service, "cancel_order") as cancel_mock:
            resp = client.post("/api/oms/order/DUP-1/cancel")

        assert resp.status_code == 409
        body = resp.json()
        assert body["error"]["name"] == "Conflict"
        candidates = body["error"]["candidates"]
        assert {c["order_id"] for c in candidates} == {"ord-a", "ord-b"}
        assert {c["store_id"] for c in candidates} == {"store-1", "store-2"}
        cancel_mock.assert_not_called()

    def test_inactive_orders_are_ignored(self, client_session):
        client, session = client_session
        _add_order(session, order_id="ord-dead", source_order_id="GONE-1",
                   status=OrderStatus.PENDING, active=False)
        resp = client.post("/api/oms/order/GONE-1/cancel")
        # Soft-deleted orders are invisible to the lookup, so the request
        # falls through to the deferred-cancel path (200) like any missing
        # order instead of cancelling the dead row.
        assert resp.status_code == 200

    def test_already_cancelled_is_idempotent(self, client_session):
        client, session = client_session
        order = _add_order(session, order_id="ord-c", source_order_id="DONE-1",
                           status=OrderStatus.CANCELLED)
        with patch.object(oms_api.order_service, "cancel_order") as cancel_mock:
            resp = client.post("/api/oms/order/DONE-1/cancel", json={"reason": "retry"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["message"] == "Order already cancelled"
        assert body["status"] == "cancelled"
        assert body["order_id"] == order.order_id
        cancel_mock.assert_not_called()

    def test_not_cancellable_maps_to_409(self, client_session):
        client, session = client_session
        _add_order(session, order_id="ord-x", source_order_id="PRINT-1",
                   status=OrderStatus.PRINTREADY)
        with patch.object(
            oms_api.order_service, "cancel_order",
            side_effect=OrderNotCancellableError("nope"),
        ):
            resp = client.post("/api/oms/order/PRINT-1/cancel")
        assert resp.status_code == 409
        assert resp.json()["error"]["name"] == "Conflict"

    def test_qpmn_failure_maps_to_502(self, client_session):
        client, session = client_session
        _add_order(session, order_id="ord-y", source_order_id="QPMN-FAIL-1",
                   status=OrderStatus.PENDING)
        with patch.object(
            oms_api.order_service, "cancel_order",
            side_effect=QPMNCancelError("QPMN cancel API failed (HTTP 500): boom"),
        ):
            resp = client.post("/api/oms/order/QPMN-FAIL-1/cancel")
        assert resp.status_code == 502
        assert resp.json()["error"]["name"] == "BadGateway"

    def test_happy_path_cancels_and_audits(self, client_session):
        client, session = client_session
        _add_order(session, order_id="ord-ok", source_order_id="OK-1",
                   status=OrderStatus.PENDING)

        def _fake_cancel(session_arg, order):
            order.status = OrderStatus.CANCELLED
            return order

        with patch.object(oms_api.order_service, "cancel_order", side_effect=_fake_cancel):
            resp = client.post("/api/oms/order/OK-1/cancel", json={"reason": "customer request"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["status"] == "cancelled"
        assert body["order_id"] == "ord-ok"
        assert body["source_order_id"] == "OK-1"

        # Inbound OMS request audited with the outcome and order linkage.
        logs = _oms_audit_logs(session)
        assert len(logs) == 1
        log = logs[0]
        assert log.direction == WebhookDirection.INBOUND
        assert log.process_status == WebhookProcessStatus.PROCESSED
        assert log.order_id == "ord-ok"
        assert log.store_order_id == "qpmn-ord-ok"
        assert log.event_status == "cancelled"
        assert log.payload["request"]["reason"] == "customer request"

        # The OMS-reported reason lands in the order's own logs.
        order = session.exec(select(Order).where(Order.order_id == "ord-ok")).one()
        actions = [entry["action"] for entry in (order.logs or [])]
        assert "oms_cancel_requested" in actions


# ---------------------------------------------------------------------------
# POST /api/oms/order/{source_order_id}/return
# ---------------------------------------------------------------------------

class TestOMSReturnOrder:
    def test_not_found(self, client_session):
        client, session = client_session
        resp = client.post("/api/oms/order/NOPE-1/return", json=RETURN_BODY)
        assert resp.status_code == 404
        assert resp.json()["error"]["name"] == "NotFound"

    def test_reason_is_required(self, client_session):
        client, _ = client_session
        body = dict(RETURN_BODY)
        del body["reason"]
        resp = client.post("/api/oms/order/SOME-1/return", json=body)
        assert resp.status_code == 422

    def test_ambiguous_source_order_id_returns_candidates(self, client_session):
        client, session = client_session
        _add_order(session, order_id="ord-a2", source_order_id="DUP-2",
                   status=OrderStatus.SHIPPED, store_id="store-1")
        _add_order(session, order_id="ord-b2", source_order_id="DUP-2",
                   status=OrderStatus.SHIPPED, store_id="store-2")
        with patch.object(oms_api.order_service, "return_order") as return_mock:
            resp = client.post("/api/oms/order/DUP-2/return", json=RETURN_BODY)
        assert resp.status_code == 409
        assert len(resp.json()["error"]["candidates"]) == 2
        return_mock.assert_not_called()

    def test_not_shipped_maps_to_409(self, client_session):
        client, session = client_session
        _add_order(session, order_id="ord-p", source_order_id="PEND-1",
                   status=OrderStatus.PENDING)
        with patch.object(
            oms_api.order_service, "return_order",
            side_effect=OrderNotReturnableError(
                "Cannot return order with status 'pending'. Only shipped orders can be returned."
            ),
        ):
            resp = client.post("/api/oms/order/PEND-1/return", json=RETURN_BODY)
        assert resp.status_code == 409
        assert "Only shipped orders can be returned" in resp.json()["error"]["message"]

    def test_qpmn_failure_maps_to_502_and_audits_failure(self, client_session):
        client, session = client_session
        _add_order(session, order_id="ord-q", source_order_id="QPMN-RET-FAIL",
                   status=OrderStatus.SHIPPED)
        with patch.object(
            oms_api.order_service, "return_order",
            side_effect=QPMNReturnError("QPMN return API failed (HTTP 500): boom"),
        ):
            resp = client.post("/api/oms/order/QPMN-RET-FAIL/return", json=RETURN_BODY)
        assert resp.status_code == 502
        assert resp.json()["error"]["name"] == "BadGateway"
        logs = _oms_audit_logs(session)
        assert logs[0].process_status == WebhookProcessStatus.FAILED

    def test_returning_is_idempotent(self, client_session):
        client, session = client_session
        _add_order(session, order_id="ord-r1", source_order_id="RETD-1",
                   status=OrderStatus.RETURNING)
        with patch.object(oms_api.order_service, "return_order") as return_mock:
            resp = client.post("/api/oms/order/RETD-1/return", json=RETURN_BODY)
        assert resp.status_code == 200
        body = resp.json()
        assert body["message"] == "Order return already requested"
        assert body["status"] == "returning"
        return_mock.assert_not_called()

    def test_return_completed_is_idempotent(self, client_session):
        client, session = client_session
        _add_order(session, order_id="ord-r2", source_order_id="RETD-2",
                   status=OrderStatus.RETURN_COMPLETED)
        with patch.object(oms_api.order_service, "return_order") as return_mock:
            resp = client.post("/api/oms/order/RETD-2/return", json=RETURN_BODY)
        assert resp.status_code == 200
        assert resp.json()["status"] == "returncompleted"
        return_mock.assert_not_called()

    def test_happy_path_returns_order_and_audits(self, client_session):
        client, session = client_session
        _add_order(session, order_id="ord-rt", source_order_id="RET-OK-1",
                   status=OrderStatus.SHIPPED)
        captured = {}

        def _fake_return(session_arg, order, return_payload):
            captured["payload"] = return_payload
            order.status = OrderStatus.RETURNING
            return order

        with patch.object(oms_api.order_service, "return_order", side_effect=_fake_return):
            resp = client.post("/api/oms/order/RET-OK-1/return", json=RETURN_BODY)

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["status"] == "returning"
        assert body["order_id"] == "ord-rt"

        # The full OMS return payload (incl. logistics fields) reaches the
        # service layer, with isFullReturn defaulting to true.
        assert captured["payload"]["reason"] == "damaged in transit"
        assert captured["payload"]["isFullReturn"] is True
        assert captured["payload"]["trackingNumber"] == "1Z999AA10123456784"
        assert captured["payload"]["carrierName"] == "FedEx"

        logs = _oms_audit_logs(session)
        assert len(logs) == 1
        assert logs[0].process_status == WebhookProcessStatus.PROCESSED
        assert logs[0].event_status == "returning"
        assert logs[0].order_id == "ord-rt"

    def test_partial_return_without_items_is_rejected(self, client_session):
        client, _ = client_session
        body = {**RETURN_BODY, "isFullReturn": False}
        resp = client.post("/api/oms/order/PART-1/return", json=body)
        assert resp.status_code == 422
        # OMS endpoints keep FastAPI's default validation-error shape.
        messages = [e["msg"] for e in resp.json()["detail"]]
        assert any("isFullReturn" in msg for msg in messages)

    def test_partial_return_item_quantity_must_be_positive(self, client_session):
        client, _ = client_session
        body = {
            **RETURN_BODY,
            "isFullReturn": False,
            "items": [{"sku": "PC-123", "quantity": 0}],
        }
        resp = client.post("/api/oms/order/PART-2/return", json=body)
        assert resp.status_code == 422

    def test_partial_return_with_items_passes_through(self, client_session):
        client, session = client_session
        _add_order(session, order_id="ord-pr", source_order_id="PART-3",
                   status=OrderStatus.SHIPPED)
        captured = {}

        def _fake_return(session_arg, order, return_payload):
            captured["payload"] = return_payload
            order.status = OrderStatus.RETURNING
            return order

        body = {
            **RETURN_BODY,
            "isFullReturn": False,
            "items": [
                {"sku": "PC-123", "quantity": 2},
                {"sku": "PC-456", "quantity": 1},
            ],
        }
        with patch.object(oms_api.order_service, "return_order", side_effect=_fake_return):
            resp = client.post("/api/oms/order/PART-3/return", json=body)

        assert resp.status_code == 200
        assert captured["payload"]["isFullReturn"] is False
        assert captured["payload"]["items"] == [
            {"sku": "PC-123", "quantity": 2},
            {"sku": "PC-456", "quantity": 1},
        ]


# ---------------------------------------------------------------------------
# Auth wiring: the OMS endpoints must be guarded by verify_oms_auth
# ---------------------------------------------------------------------------

class TestOMSAuthWiring:
    def test_missing_credentials_rejected(self, client_session):
        client, _ = client_session
        # Drop the auth override: the real verify_oms_auth runs and rejects
        # the unsigned request with 401.
        app.dependency_overrides.pop(verify_oms_auth, None)
        try:
            resp = client.post("/api/oms/order/ANY-1/cancel")
        finally:
            app.dependency_overrides[verify_oms_auth] = lambda: "test-oms-key"
        assert resp.status_code == 401
