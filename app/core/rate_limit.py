"""Per-client API rate limiting (sliding window, in-process).

Enforced inside the two inbound authentication dependencies
(``verify_oneflow_auth`` / ``verify_oms_auth``) AFTER the request's
signature has been verified — only authenticated traffic consumes a
client's budget, so an unauthenticated flood with a stolen/spoofed token
never throttles the legitimate client.

Limit resolution per client:
- ``clients.rate_limit_rps`` set  -> that many requests per second (0 = unlimited)
- ``NULL``                        -> global default ``CLIENT_RATE_LIMIT_RPS`` (20)

Global kill switch: ``CLIENT_RATE_LIMIT_ENABLED=false`` disables enforcement
entirely (load tests, incident mitigation).

Windows live in process memory. The gateway runs as a single uvicorn
process (Dockerfile CMD), so the limit is exact; if the service is ever
scaled to multiple workers or replicas, each process enforces its own
window and the effective limit multiplies — move the counters to a shared
store (e.g. Redis) at that point.
"""
import logging
import math
import threading
import time
from collections import deque
from typing import Tuple

from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)

# Sliding window length in seconds: a limit of N means "at most N requests
# in any 1-second span", which is the semantics promised for "N RPS".
_WINDOW_SECONDS = 1.0


class ClientRateLimiter:
    """Thread-safe sliding-window counter keyed by arbitrary string keys.

    Sync FastAPI dependencies run in the anyio threadpool, so all mutations
    happen under one lock; the critical section is a few microseconds per
    request, negligible at the intended limits.
    """

    def __init__(self) -> None:
        self._windows: dict[str, deque] = {}
        self._lock = threading.Lock()

    def check(self, key: str, limit_rps: int) -> Tuple[bool, float]:
        """Record one request attempt against *key*.

        Returns ``(allowed, retry_after_seconds)`` — ``retry_after_seconds``
        is 0.0 when allowed, otherwise how long until the oldest request in
        the window expires out.
        """
        if limit_rps <= 0:  # 0 = unlimited by contract
            return True, 0.0
        now = time.monotonic()
        with self._lock:
            window = self._windows.setdefault(key, deque())
            cutoff = now - _WINDOW_SECONDS
            while window and window[0] <= cutoff:
                window.popleft()
            if len(window) < limit_rps:
                window.append(now)
                return True, 0.0
            return False, window[0] + _WINDOW_SECONDS - now

    def reset(self) -> None:
        """Drop all counters (test isolation)."""
        with self._lock:
            self._windows.clear()


client_rate_limiter = ClientRateLimiter()


def effective_rate_limit_rps(client) -> int:
    """Resolve the effective per-second limit for a clients-table row."""
    limit = getattr(client, "rate_limit_rps", None)
    if limit is None:
        limit = settings.CLIENT_RATE_LIMIT_RPS
    return limit


def enforce_client_rate_limit(client, scope: str, token: str) -> None:
    """Raise the SiteFlow-shaped HTTP 429 when the client is over budget.

    ``scope`` separates credential namespaces (``oneflow`` vs ``oms``) so
    OMS calls do not eat the VFS-facing budget of the same client.
    """
    if not settings.CLIENT_RATE_LIMIT_ENABLED:
        return
    limit = effective_rate_limit_rps(client)
    if limit <= 0:
        return
    allowed, retry_after = client_rate_limiter.check(f"{scope}:{token}", limit)
    if allowed:
        return
    retry_after = max(1, math.ceil(retry_after))
    logger.warning(
        "[RateLimit] %s token '%.4s...' of store %s exceeded %s req/s - rejecting with 429 (retry after %ss)",
        scope, token, getattr(client, "store_id", None), limit, retry_after,
    )
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail={
            "success": False,
            "error": {
                "message": (
                    f"Rate limit exceeded: at most {limit} requests per second "
                    f"are allowed for this client; retry after {retry_after} seconds"
                ),
                "name": "TooManyRequests",
                "code": 429,
            },
        },
        headers={
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(limit),
            "X-Error-Code": "RATE_LIMIT_EXCEEDED",
        },
    )
