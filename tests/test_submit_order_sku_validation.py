"""Unit tests for SKU validation in POST /order (submit_order).

submit_order must reject items whose sku cannot be resolved to an active
SKU in the client's store (via source_sku exact/regex match, same rule as
POST /order/validate) BEFORE the order is persisted — parallel card orders
keep their documented items-validation skip.
"""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.api import orders as orders_api
from app.core.auth_oneflow import verify_oneflow_auth, get_client_store_id
from app.core.database import get_session
from app.main import app
from app.models.order import Order
from app.models.product import Sku


@pytest.fixture(name="client_session")
def client_session_fixture():
    """TestClient wired to a fresh in-memory DB, auth dependencies stubbed.

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
        app.dependency_overrides[verify_oneflow_auth] = lambda: None
        app.dependency_overrides[get_client_store_id] = lambda: None
        # The lifespan's create_db_and_tables() targets the real MySQL
        # engine (and seeds default rows); the tables already exist on the
        # in-memory engine above, so stub it out — keeps the whole test
        # DB-free for CI runners without a MySQL service.
        with patch("app.main.create_db_and_tables", lambda: None), TestClient(app) as client:
            yield client, session
        app.dependency_overrides.clear()


def _payload(source_order_id="SO-1", items=None):
    return {
        "destination": {"name": "qpmn"},
        "orderData": {
            "sourceOrderId": source_order_id,
            "items": items if items is not None else [{"sku": "NOPE-1"}],
        },
    }


def _add_sku(session, source_sku, active=True):
    session.add(Sku(
        sku_id=f"sku-{source_sku}", code=source_sku, product_id="prod-1",
        source_sku=source_sku, active=active,
    ))
    session.commit()


def _assert_validation_failed(resp, expected):
    assert resp.status_code == 400
    body = resp.json()
    assert body["error"]["ofError"] is True
    assert body["error"]["code"] == 208
    assert body["error"]["message"] == "Validation Failed"
    assert body["error"]["validations"] == expected


def test_invalid_sku_rejected_before_creation(client_session):
    client, session = client_session
    with patch.object(orders_api.order_service, "create_order") as create_mock:
        resp = client.post("/api/order", json=_payload(items=[{"sku": "NOPE-1"}]))
    _assert_validation_failed(resp, [{
        "path": "orderData.items.0.sku",
        "message": "Invalid or inactive SKU",
    }])
    create_mock.assert_not_called()


def test_multiple_invalid_skus_all_reported(client_session):
    client, session = client_session
    resp = client.post("/api/order", json=_payload(items=[
        {"sku": "NOPE-1"}, {"sku": "NOPE-2"},
    ]))
    _assert_validation_failed(resp, [
        {"path": "orderData.items.0.sku", "message": "Invalid or inactive SKU"},
        {"path": "orderData.items.1.sku", "message": "Invalid or inactive SKU"},
    ])


def test_empty_sku_rejected(client_session):
    client, session = client_session
    resp = client.post("/api/order", json=_payload(items=[{"sku": ""}]))
    _assert_validation_failed(resp, [{
        "path": "orderData.items.0.sku",
        "message": "SKU is required for all items",
    }])


def test_inactive_sku_rejected(client_session):
    client, session = client_session
    _add_sku(session, "PC-123", active=False)
    resp = client.post("/api/order", json=_payload(items=[{"sku": "PC-123"}]))
    _assert_validation_failed(resp, [{
        "path": "orderData.items.0.sku",
        "message": "Invalid or inactive SKU",
    }])


def test_regex_matching_sku_passes(client_session):
    client, session = client_session
    _add_sku(session, r"^PC-\d+$")
    created = Order(order_id="ord-1", source_order_id="SO-1")
    with patch.object(
        orders_api.order_service, "create_order", return_value=created
    ) as create_mock, patch.object(
        orders_api.oss_service, "upload_json_and_get_url",
        return_value="https://oss/url",
    ):
        resp = client.post("/api/order", json=_payload(items=[{"sku": "PC-123"}]))
    assert resp.status_code == 200
    assert resp.json()["_id"] == "ord-1"
    create_mock.assert_called_once()


def test_parallel_card_skips_sku_validation(client_session):
    client, session = client_session
    parent = SimpleNamespace(order_id="ord-0", source_order_id="base")
    # source_account is NOT NULL — the parallel-card branch persists the
    # created order (logs update) before responding.
    created = Order(
        order_id="ord-2", source_account="qpmn", source_order_id="base-1_S1055",
    )
    with patch.object(
        orders_api.order_service, "find_parallel_card_parent",
        return_value=parent,
    ), patch.object(
        orders_api.order_service, "create_order", return_value=created,
    ) as create_mock, patch.object(
        orders_api.oss_service, "upload_json_and_get_url",
        return_value="https://oss/url",
    ):
        resp = client.post("/api/order", json=_payload(
            source_order_id="base-1_S1055", items=[{"sku": "TOTALLY-INVALID"}],
        ))
    assert resp.status_code == 200
    create_mock.assert_called_once()
