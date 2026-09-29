"""Unit tests for OMS signed-header authentication (app/core/auth_oms.py).

The verifier resolves the presented token against the per-client
``oms_token`` column of an in-memory ``clients`` table and signs with that
client's ``oms_secret`` — no environment variables involved.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.auth_oms import verify_oms_auth
from app.core.auth_oneflow import generate_signature
from app.models.client import Client


API_KEY = "test-oms-key"
SECRET = "test-oms-secret"
# Known-answer vector: HMAC-SHA256("test-oms-secret",
# "POST /api/oms/order/ORD-001/cancel 2026-01-01T12:00:00.000Z")
KNOWN_DIGEST = "6b7141f9a02ee0c1109f67f3fd465b5b48f3e5eafea2a772d2aa49de1724245d"
KNOWN_TS = "2026-01-01T12:00:00.000Z"
PATH = "/api/oms/order/ORD-001/cancel"


@pytest.fixture(name="session")
def session_fixture():
    """Fresh in-memory DB with one active client carrying OMS credentials."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Client(
            name="OMS test client",
            store_id="store-1",
            token="oneflow-token-unrelated",
            secret="oneflow-secret-unrelated",
            oms_token=API_KEY,
            oms_secret=SECRET,
        ))
        session.commit()
        yield session


def _fresh_timestamp(offset_seconds: int = 0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _fake_request(method: str = "POST", path: str = PATH) -> SimpleNamespace:
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


def _call(request=None, authorization=None, date=None, algorithm="SHA256", session=None):
    return verify_oms_auth(
        request=request or _fake_request(),
        x_oneflow_authorization=authorization,
        x_oneflow_date=date,
        x_oneflow_algorithm=algorithm,
        session=session,
    )


class TestKnownAnswerVector:
    def test_signature_known_digest(self):
        assert generate_signature(SECRET, "POST", PATH, KNOWN_TS) == KNOWN_DIGEST

    def test_signature_depends_on_secret_path_and_timestamp(self):
        assert generate_signature("other-secret", "POST", PATH, KNOWN_TS) != KNOWN_DIGEST
        assert generate_signature(SECRET, "POST", "/api/oms/other", KNOWN_TS) != KNOWN_DIGEST
        assert generate_signature(SECRET, "PUT", PATH, KNOWN_TS) != KNOWN_DIGEST
        assert generate_signature(SECRET, "POST", PATH, "2026-01-01T12:00:01.000Z") != KNOWN_DIGEST


class TestVerifyOMSAuth:
    def test_valid_request_returns_client(self, session):
        ts = _fresh_timestamp()
        client = _call(
            authorization=f"{API_KEY}:{generate_signature(SECRET, 'POST', PATH, ts)}",
            date=ts,
            session=session,
        )
        assert isinstance(client, Client)
        assert client.oms_token == API_KEY
        assert client.store_id == "store-1"

    def test_signature_is_method_and_path_specific(self, session):
        ts = _fresh_timestamp()
        # Signed for POST but the request carries the wrong HTTP method.
        with pytest.raises(HTTPException) as exc_info:
            _call(
                request=_fake_request(method="PUT"),
                authorization=f"{API_KEY}:{generate_signature(SECRET, 'POST', PATH, ts)}",
                date=ts,
                session=session,
            )
        assert exc_info.value.headers["X-Error-Code"] == "INVALID_SIGNATURE"

    def test_missing_authorization_header(self, session):
        with pytest.raises(HTTPException) as exc_info:
            _call(authorization=None, date=_fresh_timestamp(), session=session)
        assert exc_info.value.status_code == 401
        assert exc_info.value.headers["X-Error-Code"] == "MISSING_AUTHORIZATION"

    def test_missing_date_header(self, session):
        ts = _fresh_timestamp()
        with pytest.raises(HTTPException) as exc_info:
            _call(
                authorization=f"{API_KEY}:{generate_signature(SECRET, 'POST', PATH, ts)}",
                date=None,
                session=session,
            )
        assert exc_info.value.status_code == 401
        assert exc_info.value.headers["X-Error-Code"] == "MISSING_DATE"

    def test_missing_algorithm_header(self, session):
        ts = _fresh_timestamp()
        with pytest.raises(HTTPException) as exc_info:
            _call(
                authorization=f"{API_KEY}:{generate_signature(SECRET, 'POST', PATH, ts)}",
                date=ts,
                algorithm=None,
                session=session,
            )
        assert exc_info.value.status_code == 401
        assert exc_info.value.headers["X-Error-Code"] == "MISSING_ALGORITHM"

    def test_unsupported_algorithm(self, session):
        ts = _fresh_timestamp()
        with pytest.raises(HTTPException) as exc_info:
            _call(
                authorization=f"{API_KEY}:{generate_signature(SECRET, 'POST', PATH, ts)}",
                date=ts,
                algorithm="MD5",
                session=session,
            )
        assert exc_info.value.status_code == 401
        assert exc_info.value.headers["X-Error-Code"] == "UNSUPPORTED_ALGORITHM"

    def test_invalid_authorization_format(self, session):
        with pytest.raises(HTTPException) as exc_info:
            _call(authorization="no-colon-here", date=_fresh_timestamp(), session=session)
        assert exc_info.value.status_code == 401
        assert exc_info.value.headers["X-Error-Code"] == "INVALID_AUTHORIZATION_FORMAT"

    def test_empty_token_or_signature(self, session):
        ts = _fresh_timestamp()
        with pytest.raises(HTTPException) as exc_info:
            _call(
                authorization=f":{generate_signature(SECRET, 'POST', PATH, ts)}",
                date=ts,
                session=session,
            )
        assert exc_info.value.headers["X-Error-Code"] == "INVALID_AUTHORIZATION_FORMAT"

    def test_unknown_token(self, session):
        """A token no client carries is rejected, even with a valid signature."""
        ts = _fresh_timestamp()
        with pytest.raises(HTTPException) as exc_info:
            _call(
                authorization=f"wrong-key:{generate_signature(SECRET, 'POST', PATH, ts)}",
                date=ts,
                session=session,
            )
        assert exc_info.value.status_code == 403
        assert exc_info.value.headers["X-Error-Code"] == "INVALID_TOKEN"

    def test_oneflow_token_not_accepted_as_oms_token(self, session):
        """The OneFlow client token lives in a different column: presenting it
        as the OMS token must not authenticate."""
        ts = _fresh_timestamp()
        with pytest.raises(HTTPException) as exc_info:
            _call(
                authorization=(
                    f"oneflow-token-unrelated:"
                    f"{generate_signature(SECRET, 'POST', PATH, ts)}"
                ),
                date=ts,
                session=session,
            )
        assert exc_info.value.headers["X-Error-Code"] == "INVALID_TOKEN"

    def test_client_without_oms_secret_rejected(self, session):
        """A client whose oms_token matches but oms_secret is NULL cannot
        authenticate (credentials half-configured)."""
        session.add(Client(
            name="half-configured",
            store_id="store-2",
            token="oneflow-token-2",
            secret="oneflow-secret-2",
            oms_token="half-way-token",
            oms_secret=None,
        ))
        session.commit()
        ts = _fresh_timestamp()
        with pytest.raises(HTTPException) as exc_info:
            _call(
                authorization=f"half-way-token:{generate_signature('whatever', 'POST', PATH, ts)}",
                date=ts,
                session=session,
            )
        assert exc_info.value.headers["X-Error-Code"] == "INVALID_TOKEN"

    def test_inactive_client_rejected(self, session):
        client = session.exec(select(Client).where(Client.store_id == "store-1")).one()
        client.is_active = False
        session.add(client)
        session.commit()

        ts = _fresh_timestamp()
        with pytest.raises(HTTPException) as exc_info:
            _call(
                authorization=f"{API_KEY}:{generate_signature(SECRET, 'POST', PATH, ts)}",
                date=ts,
                session=session,
            )
        assert exc_info.value.headers["X-Error-Code"] == "INVALID_TOKEN"

    def test_expired_timestamp(self, session):
        stale_ts = _fresh_timestamp(offset_seconds=-3600)
        with pytest.raises(HTTPException) as exc_info:
            _call(
                authorization=f"{API_KEY}:{generate_signature(SECRET, 'POST', PATH, stale_ts)}",
                date=stale_ts,
                session=session,
            )
        assert exc_info.value.status_code == 401
        assert exc_info.value.headers["X-Error-Code"] == "EXPIRED_TIMESTAMP"

    def test_invalid_signature(self, session):
        ts = _fresh_timestamp()
        with pytest.raises(HTTPException) as exc_info:
            _call(authorization=f"{API_KEY}:deadbeef", date=ts, session=session)
        assert exc_info.value.status_code == 403
        assert exc_info.value.headers["X-Error-Code"] == "INVALID_SIGNATURE"

    def test_signed_with_oneflow_secret_rejected(self, session):
        """Credential-pair independence: a request signed with the client's
        OneFlow secret must fail OMS verification (which uses oms_secret)."""
        ts = _fresh_timestamp()
        with pytest.raises(HTTPException) as exc_info:
            _call(
                authorization=(
                    f"{API_KEY}:"
                    f"{generate_signature('oneflow-secret-unrelated', 'POST', PATH, ts)}"
                ),
                date=ts,
                session=session,
            )
        assert exc_info.value.headers["X-Error-Code"] == "INVALID_SIGNATURE"
