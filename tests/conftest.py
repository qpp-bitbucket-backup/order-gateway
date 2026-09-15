"""Pytest configuration and fixtures."""
import pytest
from sqlalchemy import text


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
