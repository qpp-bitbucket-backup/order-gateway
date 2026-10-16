"""Unit tests for the platform OMS credentials endpoints (app/api/clients.py).

TestClient wired to a fresh in-memory DB with ``require_admin`` stubbed to a
fake admin user whose hashed password matches ``PASSWORD`` (the reveal
endpoint re-checks it); update semantics (rotate / explicit / 422s) are
asserted against the persisted client row.
"""
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.auth_admin import verify_admin_key
from app.core.auth_jwt import require_admin
from app.core.database import get_session
from app.core.security import get_password_hash
from app.main import app
from app.models.client import Client


PASSWORD = "admin-pass-123"


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
            name="OMS client",
            store_id="store-1",
            token="of-token",
            secret="of-secret",
            oms_token="oms-token-old",
            oms_secret="oms-secret-old-0000000000000000000000",
        ))
        session.commit()

        fake_admin = type("FakeAdmin", (), {
            "id": 1,
            "username": "admin",
            "role": "ADMIN",
            "hashed_password": get_password_hash(PASSWORD),
        })()
        app.dependency_overrides[get_session] = lambda: session
        app.dependency_overrides[require_admin] = lambda: fake_admin
        with patch("app.main.create_db_and_tables", lambda: None), TestClient(app) as client:
            yield client, session
        app.dependency_overrides.clear()


def _get_client(session) -> Client:
    return session.exec(select(Client).where(Client.store_id == "store-1")).one()


REVEAL_URL = "/api/platform/clients/oms_secret"
UPDATE_URL = "/api/platform/clients/oms_secret"


class TestRevealOMSCredentials:
    def test_wrong_password_rejected(self, client_session):
        client, _ = client_session
        resp = client.post(REVEAL_URL, json={"store_id": "store-1", "password": "nope"})
        assert resp.status_code == 403

    def test_reveals_token_and_secret(self, client_session):
        client, _ = client_session
        resp = client.post(REVEAL_URL, json={"store_id": "store-1", "password": PASSWORD})
        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["oms_token"] == "oms-token-old"
        assert body["oms_secret"] == "oms-secret-old-0000000000000000000000"

    def test_unknown_store_404(self, client_session):
        client, _ = client_session
        resp = client.post(REVEAL_URL, json={"store_id": "nope", "password": PASSWORD})
        assert resp.status_code == 404


class TestUpdateOMSCredentials:
    def test_rotate_generates_new_pair(self, client_session):
        client, session = client_session
        resp = client.put(UPDATE_URL, json={"store_id": "store-1", "rotate": True})
        assert resp.status_code == 200
        body = resp.json()
        # Server-generated: 12-char hex token / 32-char hex secret.
        assert body["oms_token"] != "oms-token-old"
        assert len(body["oms_token"]) == 12
        assert body["oms_secret"] != "oms-secret-old-0000000000000000000000"
        assert len(body["oms_secret"]) == 32

        stored = _get_client(session)
        assert stored.oms_token == body["oms_token"]
        assert stored.oms_secret == body["oms_secret"]

    def test_explicit_values_persisted(self, client_session):
        client, session = client_session
        resp = client.put(UPDATE_URL, json={
            "store_id": "store-1",
            "oms_token": "explicit-token",
            "oms_secret": "x" * 32,
        })
        assert resp.status_code == 200
        body = resp.json()
        assert body["oms_token"] == "explicit-token"
        assert body["oms_secret"] == "x" * 32

        stored = _get_client(session)
        assert stored.oms_token == "explicit-token"
        assert stored.oms_secret == "x" * 32

    def test_token_only_update_never_echoes_secret(self, client_session):
        client, session = client_session
        resp = client.put(UPDATE_URL, json={"store_id": "store-1", "oms_token": "token-2"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["oms_token"] == "token-2"
        # The unchanged secret must not be echoed back.
        assert body["oms_secret"] is None
        assert _get_client(session).oms_secret == "oms-secret-old-0000000000000000000000"

    def test_rotate_with_explicit_values_is_422(self, client_session):
        client, _ = client_session
        resp = client.put(UPDATE_URL, json={
            "store_id": "store-1",
            "rotate": True,
            "oms_token": "conflict",
        })
        assert resp.status_code == 422

    def test_noop_update_is_422(self, client_session):
        client, _ = client_session
        resp = client.put(UPDATE_URL, json={"store_id": "store-1"})
        assert resp.status_code == 422

    def test_short_secret_is_422(self, client_session):
        client, _ = client_session
        resp = client.put(UPDATE_URL, json={"store_id": "store-1", "oms_secret": "short"})
        assert resp.status_code == 422

    def test_unknown_store_404(self, client_session):
        client, _ = client_session
        resp = client.put(UPDATE_URL, json={"store_id": "nope", "rotate": True})
        assert resp.status_code == 404


class TestCreateClientGeneratesOMSCredentials:
    """Both creation endpoints generate the OMS credential pair together
    with the OneFlow pair — a new client can authenticate against the OMS
    endpoints right away, the oms_secret being shown only once."""

    def test_platform_create_generates_oms_credentials(self, client_session):
        client, session = client_session
        with patch(
            "app.api.clients.order_service.verify_store_credentials",
            return_value=(True, ""),
        ):
            resp = client.post("/api/platform/clients", json={
                "name": "new-store",
                "store_id": "store-new",
                "store_key": "key-new",
                "description": "created via platform API",
            })
        assert resp.status_code == 201
        body = resp.json()
        assert body["client"]["oms_token"]
        assert body["oms_secret"]
        # Server-generated shapes: 12-char hex token / 32-char hex secret.
        assert len(body["client"]["oms_token"]) == 12
        assert len(body["oms_secret"]) == 32
        assert body["oms_secret"] != body["secret"]

        stored = session.exec(
            select(Client).where(Client.store_id == "store-new")
        ).one()
        assert stored.oms_token == body["client"]["oms_token"]
        assert stored.oms_secret == body["oms_secret"]

    def test_admin_key_create_generates_oms_credentials(self, client_session):
        client, session = client_session
        app.dependency_overrides[verify_admin_key] = lambda: None
        try:
            resp = client.post("/api/client", json={
                "name": "admin-created",
                "store_id": "store-admin",
            })
        finally:
            app.dependency_overrides.pop(verify_admin_key, None)
        assert resp.status_code == 201
        body = resp.json()
        assert body["client"]["oms_token"]
        assert len(body["oms_secret"]) == 32

        stored = session.exec(
            select(Client).where(Client.store_id == "store-admin")
        ).one()
        assert stored.oms_token == body["client"]["oms_token"]
        assert stored.oms_secret == body["oms_secret"]
