"""Unit tests for the deferred OMS cancel flow (order_cancel_list).

Covers the three legs of the feature:
1. Model defaults — a fresh record is pending with no order linkage.
2. OMS cancel API no-order path — the request is acknowledged (200) and
   remembered as a pending row, idempotently per sourceOrderId, and only
   on the cancel endpoint.
3. order_publishing worker consumption — when the VFS-created order finally
   arrives, a pending record cancels it locally instead of publishing, and a
   processed record leaves the normal publishing path untouched.
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
from app.models.order_cancel_record import OrderCancelRecord, OrderCancelRecordStatus
from app.tasks import orders as orders_tasks


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

class TestOrderCancelRecordModel:
    def test_fresh_record_is_pending(self):
        record = OrderCancelRecord(source_order_id="S-1", reason="changed mind")
        assert record.status == OrderCancelRecordStatus.PENDING
        assert record.order_id is None
        assert record.processed_message is None
        assert record.reason == "changed mind"


# ---------------------------------------------------------------------------
# OMS cancel API no-order path -> 200 + pending rows
# ---------------------------------------------------------------------------

@pytest.fixture(name="client_session")
def client_session_fixture():
    """TestClient wired to a fresh in-memory DB, OMS auth stubbed."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[verify_oms_auth] = lambda: "test-oms-key"
        with patch("app.main.create_db_and_tables", lambda: None), TestClient(app) as client:
            yield client, session
        app.dependency_overrides.clear()


def _cancel_records(session):
    return list(session.exec(select(OrderCancelRecord)).all())


class TestOMSCancelMissingOrderDefersCancel:
    def test_missing_order_returns_200_and_creates_pending_record(self, client_session):
        client, session = client_session
        resp = client.post(
            "/api/oms/order/LATE-1/cancel", json={"reason": "customer changed mind"}
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["order_id"] is None
        assert body["status"] is None
        assert "recorded" in body["message"]
        # The deferred request is audited and remembered as pending.
        records = _cancel_records(session)
        assert len(records) == 1
        record = records[0]
        assert record.source_order_id == "LATE-1"
        assert record.status == OrderCancelRecordStatus.PENDING
        assert record.reason == "customer changed mind"
        assert record.order_id is None

    def test_repeated_deferred_cancel_refreshes_reason_without_duplicates(self, client_session):
        client, session = client_session
        client.post("/api/oms/order/LATE-2/cancel", json={"reason": "first reason"})
        resp = client.post("/api/oms/order/LATE-2/cancel", json={"reason": "second reason"})
        assert resp.status_code == 200
        records = _cancel_records(session)
        assert len(records) == 1
        assert records[0].reason == "second reason"
        assert records[0].status == OrderCancelRecordStatus.PENDING

    def test_successful_cancel_writes_no_record(self, client_session):
        client, session = client_session
        order = Order(
            order_id="ord-have",
            source_account="oms-test-account",
            source_order_id="HAVE-1",
            status=OrderStatus.PENDING,
            store_id="store-1",
            is_active=True,
        )
        session.add(order)
        session.commit()

        def _fake_cancel(session_arg, order):
            order.status = OrderStatus.CANCELLED
            return order

        with patch.object(oms_api.order_service, "cancel_order", side_effect=_fake_cancel):
            resp = client.post("/api/oms/order/HAVE-1/cancel", json={"reason": "normal"})

        assert resp.status_code == 200
        assert _cancel_records(session) == []

    def test_return_404_writes_no_record(self, client_session):
        client, session = client_session
        body = {"reason": "damaged in transit"}
        resp = client.post("/api/oms/order/LATE-3/return", json=body)
        assert resp.status_code == 404
        # Only the cancel endpoint defers requests; a return cannot be
        # deferred (returns need a shipped order, not an absent one).
        assert _cancel_records(session) == []


# ---------------------------------------------------------------------------
# order_publishing worker consumption
# ---------------------------------------------------------------------------

@pytest.fixture(name="worker_env")
def worker_env_fixture(monkeypatch):
    """In-memory engine wired into the task module + outbound calls captured.

    publish_order opens its own Session(engine); pointing that module-level
    engine at the shared in-memory connection keeps rows created here visible
    to the task. The email enqueue and the chained validate_order dispatch
    are captured instead of hitting broker/SMTP.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    monkeypatch.setattr(orders_tasks, "engine", engine)

    emails: list = []

    def _capture_email(session, order, from_status, new_status, level, message):
        emails.append((from_status, new_status, message))

    monkeypatch.setattr(orders_tasks, "enqueue_status_change_email", _capture_email)
    chained: list = []
    monkeypatch.setattr(
        orders_tasks.validate_order, "apply_async",
        lambda *a, **kw: chained.append((a, kw)),
    )
    monkeypatch.setattr(
        orders_tasks.client_service, "get_store_key_by_id", lambda store_id: "store-key"
    )
    return {
        "engine": engine,
        "emails": emails,
        "chained": chained,
    }


def _add_worker_order(session, *, order_id="ord-w1", source_order_id="LATE-W1"):
    order = Order(
        order_id=order_id,
        source_account="vfs-test-account",
        source_order_id=source_order_id,
        status=OrderStatus.RECEIVED,
        store_id="store-1",
        store_order_id=f"qpmn-{order_id}",
        order_data={"items": []},
        is_active=True,
    )
    session.add(order)
    session.commit()
    return order


class TestPublishOrderConsumesPendingCancel:
    def test_pending_record_cancels_order_instead_of_publishing(self, worker_env):
        with Session(worker_env["engine"]) as session:
            order = _add_worker_order(session)
            record = OrderCancelRecord(
                source_order_id="LATE-W1", reason="cancelled in OMS before arrival"
            )
            session.add(record)
            session.commit()

            result = orders_tasks.publish_order({"order_id": order.order_id})

            assert result is True
            session.refresh(order)
            session.refresh(record)
            # The order is cancelled locally, never published.
            assert order.status == OrderStatus.CANCELLED
            cancel_entries = [
                entry for entry in (order.logs or [])
                if entry.get("action") == "order_cancelled"
            ]
            assert cancel_entries, "expected an order_cancelled log entry"
            assert cancel_entries[0]["source"] == "pending_oms_cancel"
            assert cancel_entries[0]["worker"] == "order_publishing"
            assert "cancelled in OMS before arrival" in cancel_entries[0]["message"]
            # The record is stamped processed and linked to the order.
            assert record.status == OrderCancelRecordStatus.PROCESSED
            assert record.order_id == order.order_id
            assert record.processed_message

        # The status-change email path was triggered; nothing chained onward.
        assert len(worker_env["emails"]) == 1
        assert worker_env["emails"][0][1] == OrderStatus.CANCELLED
        assert not worker_env["chained"]

    def test_processed_record_leaves_publishing_untouched(self, worker_env):
        with Session(worker_env["engine"]) as session:
            order = _add_worker_order(session, order_id="ord-w2",
                                      source_order_id="DONE-W2")
            session.add(OrderCancelRecord(
                source_order_id="DONE-W2",
                status=OrderCancelRecordStatus.PROCESSED,
                order_id="ord-older",
            ))
            session.commit()

            result = orders_tasks.publish_order({"order_id": order.order_id})

            assert result is True
            session.refresh(order)
            # Already-consumed records must not re-cancel: the order goes
            # down the normal publishing path (RECEIVED -> PENDING) and
            # chains to validate_order.
            assert order.status == OrderStatus.PENDING
            assert order.status != OrderStatus.CANCELLED

        assert len(worker_env["chained"]) == 1
        # The only notification on this path is the PENDING publishing-start
        # entry (email-muted in production); no CANCELLED email ever fired.
        assert worker_env["emails"][0][1] == OrderStatus.PENDING

    def test_no_record_leaves_publishing_untouched(self, worker_env):
        with Session(worker_env["engine"]) as session:
            order = _add_worker_order(session, order_id="ord-w3",
                                      source_order_id="PLAIN-W3")
            result = orders_tasks.publish_order({"order_id": order.order_id})
            assert result is True
            session.refresh(order)
            assert order.status == OrderStatus.PENDING
