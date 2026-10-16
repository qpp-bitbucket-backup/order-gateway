"""Tests for the per-client API rate limiter (app/core/rate_limit.py) and
its enforcement inside the OneFlow / OMS authentication dependencies."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.core.auth_oms import verify_oms_auth
from app.core.auth_oneflow import (
    generate_signature,
    get_client_store_id,
    verify_oneflow_auth,
)
from app.core.config import settings
from app.core.rate_limit import (
    ClientRateLimiter,
    client_rate_limiter,
    effective_rate_limit_rps,
    enforce_client_rate_limit,
)
from app.models.client import Client

SECRET = "rate-limit-test-secret"
TOKEN = "rl-token"
OMS_TOKEN = "rl-oms-token"
OMS_SECRET = "rate-limit-oms-secret"


def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _fake_request(method: str = "POST", path: str = "/api/order") -> SimpleNamespace:
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


def _fake_client(rate_limit_rps=None) -> SimpleNamespace:
    return SimpleNamespace(
        secret=SECRET, rate_limit_rps=rate_limit_rps, store_id="store-rl"
    )


@pytest.fixture(autouse=True)
def _enable_rate_limit(monkeypatch):
    """Re-enable the limiter (conftest disables it suite-wide) and start
    every test from clean counters with a deterministic default limit."""
    monkeypatch.setattr(settings, "CLIENT_RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "CLIENT_RATE_LIMIT_RPS", 20)
    client_rate_limiter.reset()
    yield
    client_rate_limiter.reset()


class TestClientRateLimiter:
    """The sliding-window counter itself."""

    def test_allows_up_to_limit_then_rejects(self):
        limiter = ClientRateLimiter()
        results = [limiter.check("k", 3)[0] for _ in range(4)]
        assert results == [True, True, True, False]

    def test_rejected_request_reports_retry_after_within_one_second(self):
        limiter = ClientRateLimiter()
        for _ in range(2):
            limiter.check("k", 2)
        allowed, retry_after = limiter.check("k", 2)
        assert allowed is False
        assert 0 < retry_after <= 1.0

    def test_window_slides(self):
        # Rejected attempts are not recorded, so once the oldest allowed
        # request expires out of the window the budget frees up again.
        with patch("app.core.rate_limit.time.monotonic", side_effect=[1.0, 1.1, 1.2, 2.05]):
            limiter = ClientRateLimiter()
            assert limiter.check("k", 2)[0] is True   # t=1.0
            assert limiter.check("k", 2)[0] is True   # t=1.1
            assert limiter.check("k", 2)[0] is False  # t=1.2 (both in window)
            assert limiter.check("k", 2)[0] is True   # t=2.05 (t=1.0 expired)

    def test_zero_limit_is_unlimited(self):
        limiter = ClientRateLimiter()
        assert all(limiter.check("k", 0)[0] for _ in range(100))

    def test_keys_are_independent(self):
        limiter = ClientRateLimiter()
        for _ in range(3):
            limiter.check("a", 3)
        assert limiter.check("b", 1)[0] is True


class TestEffectiveRateLimitRps:
    """Limit resolution: per-client override vs global default."""

    def test_client_override_wins(self):
        assert effective_rate_limit_rps(_fake_client(rate_limit_rps=5)) == 5

    def test_null_falls_back_to_global_default(self):
        assert effective_rate_limit_rps(_fake_client(None)) == 20

    def test_zero_means_unlimited(self):
        assert effective_rate_limit_rps(_fake_client(0)) == 0


class TestEnforceClientRateLimit:
    """The 429 raised on over-budget requests."""

    def test_within_limit_does_not_raise(self):
        client = _fake_client(rate_limit_rps=3)
        for _ in range(3):
            enforce_client_rate_limit(client, scope="test", token=TOKEN)

    def test_over_limit_raises_429_siteflow_shape(self):
        client = _fake_client(rate_limit_rps=1)
        enforce_client_rate_limit(client, scope="test", token=TOKEN)
        with pytest.raises(HTTPException) as exc:
            enforce_client_rate_limit(client, scope="test", token=TOKEN)
        assert exc.value.status_code == 429
        # SiteFlow error shape, so VFS/OMS clients parse it like any other
        # rejection (unified error response format per project convention).
        assert exc.value.detail["success"] is False
        assert exc.value.detail["error"]["name"] == "TooManyRequests"
        assert exc.value.detail["error"]["code"] == 429
        assert exc.value.headers["Retry-After"] == "1"
        assert exc.value.headers["X-RateLimit-Limit"] == "1"
        assert exc.value.headers["X-Error-Code"] == "RATE_LIMIT_EXCEEDED"

    def test_zero_limit_never_raises(self):
        client = _fake_client(rate_limit_rps=0)
        for _ in range(50):
            enforce_client_rate_limit(client, scope="test", token=TOKEN)

    def test_disabled_globally_is_noop(self, monkeypatch):
        monkeypatch.setattr(settings, "CLIENT_RATE_LIMIT_ENABLED", False)
        client = _fake_client(rate_limit_rps=1)
        for _ in range(50):
            enforce_client_rate_limit(client, scope="test", token=TOKEN)


class TestVerifyOneflowAuthEnforcement:
    """verify_oneflow_auth consumes the client's budget only after the
    signature has been verified."""

    @staticmethod
    def _call(authorization: str = None, ts: str = None):
        ts = ts or _ts()
        return verify_oneflow_auth(
            request=_fake_request(),
            session=None,
            x_oneflow_authorization=authorization
            or f"{TOKEN}:{generate_signature(SECRET, 'POST', '/api/order', ts)}",
            x_oneflow_date=ts,
            x_oneflow_algorithm="SHA256",
        )

    def test_requests_beyond_limit_get_429(self):
        client = _fake_client(rate_limit_rps=2)
        with patch("app.core.auth_oneflow._get_client_by_token", return_value=client):
            assert self._call() == TOKEN
            assert self._call() == TOKEN
            with pytest.raises(HTTPException) as exc:
                self._call()
        assert exc.value.status_code == 429

    def test_unauthenticated_requests_do_not_consume_budget(self):
        # Bad signatures are rejected before the limiter runs — a spoofed
        # token flood must never throttle the legitimate client.
        client = _fake_client(rate_limit_rps=1)
        ts = _ts()
        with patch("app.core.auth_oneflow._get_client_by_token", return_value=client):
            for _ in range(5):
                with pytest.raises(HTTPException) as exc:
                    self._call(authorization=f"{TOKEN}:bad-signature", ts=ts)
                assert exc.value.status_code == 403
            assert self._call(ts=ts) == TOKEN  # budget still intact

    def test_null_limit_uses_global_default(self):
        client = _fake_client(rate_limit_rps=None)  # -> default 20
        with patch("app.core.auth_oneflow._get_client_by_token", return_value=client):
            for _ in range(20):
                self._call()
            with pytest.raises(HTTPException) as exc:
                self._call()
        assert exc.value.status_code == 429
        assert exc.value.headers["X-RateLimit-Limit"] == "20"


class TestVerifyOmsAuthEnforcement:
    """verify_oms_auth enforces in a separate 'oms' namespace."""

    @pytest.fixture()
    def oms_session(self):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        SQLModel.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(Client(
                name="rl-oms",
                store_id="store-rl",
                token="unused-oneflow-token",
                secret="unused-secret",
                oms_token=OMS_TOKEN,
                oms_secret=OMS_SECRET,
                rate_limit_rps=1,
            ))
            session.commit()
            yield session

    def _call(self, session):
        ts = _ts()
        return verify_oms_auth(
            request=_fake_request(method="POST", path="/api/oms/order/X/cancel"),
            session=session,
            x_oneflow_authorization=(
                f"{OMS_TOKEN}:{generate_signature(OMS_SECRET, 'POST', '/api/oms/order/X/cancel', ts)}"
            ),
            x_oneflow_date=ts,
            x_oneflow_algorithm="SHA256",
        )

    def test_second_request_within_window_gets_429(self, oms_session):
        assert self._call(oms_session).oms_token == OMS_TOKEN
        with pytest.raises(HTTPException) as exc:
            self._call(oms_session)
        assert exc.value.status_code == 429
        assert exc.value.headers["X-Error-Code"] == "RATE_LIMIT_EXCEEDED"

    def test_oms_budget_is_separate_from_oneflow(self, oms_session):
        # Exhaust the oms namespace...
        self._call(oms_session)
        with pytest.raises(HTTPException) as exc:
            self._call(oms_session)
        assert exc.value.status_code == 429
        # ...the same client's oneflow budget is untouched.
        client = _fake_client(rate_limit_rps=1)
        with patch("app.core.auth_oneflow._get_client_by_token", return_value=client):
            assert TestVerifyOneflowAuthEnforcement._call() == TOKEN


class TestSingleEnforcementPerRequest:
    """Regression for the QA S8 finding (2026-10-09): with the router-level
    ``verify_oneflow_auth`` dependency AND ``get_client_store_id`` each
    running the limiter, every request consumed TWO quota slots through two
    non-atomic checks — under overload, interleaved requests orphaned each
    other's slots and admitted throughput collapsed to ~0 instead of
    throttling to the configured rate. The fix reuses the cached dependency
    result, so one request = one check = one slot.
    """

    def _build_app(self):
        from fastapi import APIRouter, Depends, FastAPI
        from fastapi.testclient import TestClient

        from app.core.database import get_session

        router = APIRouter(
            prefix="/api",
            dependencies=[Depends(verify_oneflow_auth)],
        )

        @router.get("/ping")
        def ping(store_id: str = Depends(get_client_store_id)):
            return {"store_id": store_id}

        app = FastAPI()
        app.include_router(router)

        def _stub_session():
            yield None

        app.dependency_overrides[get_session] = _stub_session
        return TestClient(app), app

    @staticmethod
    def _headers():
        ts = _ts()
        return {
            "x-oneflow-authorization": (
                f"{TOKEN}:{generate_signature(SECRET, 'GET', '/api/ping', ts)}"
            ),
            "x-oneflow-date": ts,
            "x-oneflow-algorithm": "SHA256",
        }

    def test_one_request_consumes_exactly_one_slot(self):
        """limit=2: two requests pass, the third is 429 — with double
        enforcement per request the second request would already be 429."""
        client_row = _fake_client(rate_limit_rps=2)
        lookups = {"n": 0}

        def _counting_lookup(session, token):
            lookups["n"] += 1
            return client_row

        test_client, _ = self._build_app()
        with patch("app.core.auth_oneflow._get_client_by_token", side_effect=_counting_lookup):
            assert test_client.get("/api/ping", headers=self._headers()).status_code == 200
            assert test_client.get("/api/ping", headers=self._headers()).status_code == 200
            resp = test_client.get("/api/ping", headers=self._headers())

        assert resp.status_code == 429
        assert resp.headers["Retry-After"] == "1"
        assert resp.headers["X-RateLimit-Limit"] == "2"
        # Admitted requests do 2 client lookups each (one inside
        # verify_oneflow_auth, one inside get_client_store_id — the dependency
        # cache means verify itself runs once); the 429'd request still does
        # its 1 auth lookup before the limiter rejects it. The old
        # double-verification shape would 429 request #2 already.
        assert lookups["n"] == 5

    def test_rejected_request_does_not_consume_slots(self):
        """The 429'd request consumes no slot: once the recorded timestamp
        ages out of the window the budget frees again (steady ~limit/s
        throttling instead of a blackout). The window slide is simulated by
        clearing the deque directly — patching time.monotonic globally would
        also hit TestClient/httpx internals."""
        client_row = _fake_client(rate_limit_rps=1)
        test_client, _ = self._build_app()
        with patch("app.core.auth_oneflow._get_client_by_token", return_value=client_row):
            assert test_client.get("/api/ping", headers=self._headers()).status_code == 200
            assert test_client.get("/api/ping", headers=self._headers()).status_code == 429
            # Simulate the 1s window having slid (oldest entry expired).
            with client_rate_limiter._lock:
                client_rate_limiter._windows[f"oneflow:{TOKEN}"].clear()
            assert test_client.get("/api/ping", headers=self._headers()).status_code == 200
