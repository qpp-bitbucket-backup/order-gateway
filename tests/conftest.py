"""Pytest configuration and fixtures."""
import pytest
from sqlalchemy import text


def pytest_addoption(parser):
    """Custom CLI options for debug dump tests.

    --order-id selects a real order for tests/test_legacy_api_payload.py::
    test_dump_real_order_payload (dump the legacy payload for that order).

        python -m pytest tests/test_legacy_api_payload.py -k real -s --order-id=<order_id>
    """
    parser.addoption(
        "--order-id",
        action="store",
        default=None,
        help="Real order_id for payload dump tests (see test_dump_real_order_payload)",
    )


@pytest.fixture(scope="session")
def order_id_option(request):
    """Value of the --order-id CLI option (None when not passed)."""
    return request.config.getoption("--order-id")


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
