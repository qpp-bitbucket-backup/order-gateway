"""Pytest configuration and fixtures."""
import pytest
from sqlalchemy import text


@pytest.fixture(autouse=True)
def _rate_limit_isolated(monkeypatch):
    """Keep the per-client rate limiter out of unrelated tests.

    Rate limiting is enforced inside the auth dependencies, so enabling it
    suite-wide would make unrelated tests flaky (shared token, shared 1s
    window). tests/test_rate_limit.py re-enables it per test; the counters
    are reset around every test either way.
    """
    from app.core.config import settings
    from app.core.rate_limit import client_rate_limiter

    monkeypatch.setattr(settings, "CLIENT_RATE_LIMIT_ENABLED", False)
    client_rate_limiter.reset()
    yield
    client_rate_limiter.reset()


@pytest.fixture(scope="session")
def db_engine():
    """The real application engine (``app.core.database.engine``).

    Probes connectivity first and SKIPS the requesting test when MySQL is
    unreachable (e.g. CI pipelines without a database service) instead of
    failing with OperationalError 2003 — locally with a running MySQL the
    tests execute as before.
    """
    from app.core.database import engine

    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # unreachable / auth failed / driver error
        pytest.skip(f"MySQL not available ({exc}) — DB-dependent test skipped")
    return engine
