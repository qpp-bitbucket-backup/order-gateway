"""VFS service for sending order status postback webhooks (SiteFlow style)."""
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import httpx

from app.models.order import Order

logger = logging.getLogger(__name__)
USE_MOCK = False


class VFSService:
    """Service for pushing order status postbacks to a per-order VFS webhook endpoint."""

    def send_status_postback(
        self,
        order: Order,
        event_status: str,
        shipments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        POST an order status postback to VFS.

        URL comes from ``order.order_data["postbackAddress"]``, set per-order
        at order creation — diverges from HP Site Flow's documented behavior
        (docs/system-design-and-postback-investigation.md §8.1). No fallback:
        orders without a postbackAddress are skipped.

        Auth: none — confirmed empirically. The shared OMS endpoint 401s
        without HUB4, but the dedicated VFS webhook path accepts plain
        unsigned JSON.

        Returns ``success=False`` on non-retryable failures (4xx, missing
        postbackAddress, business error); raises on 5xx/network so the
        caller can retry.
        """
        postback_url = (order.order_data or {}).get("postbackAddress")
        if not postback_url:
            logger.warning(
                "[VFS] No postbackAddress on order %s – skipping postback.", order.order_id
            )
            return {"success": False, "message": "postbackAddress not set on order"}

        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "sourceOrderId": order.source_order_id,
            "status": event_status,
            "shipments": shipments or [],
        }

        if USE_MOCK:
            return _mock_postback_response(order.source_order_id, event_status, {}, payload)

        logger.info(
            "[VFS] POST postback status=%s sourceOrderId=%s url=%s",
            event_status,
            order.source_order_id,
            postback_url,
        )

        request_headers = {"Content-Type": "application/json"}

        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            response = client.post(postback_url, json=payload)

        # 4xx — business error, do not retry.
        if 400 <= response.status_code < 500:
            logger.warning(
                "[VFS] Postback returned %s for order %s: %s",
                response.status_code,
                order.source_order_id,
                response.text,
            )
            return {
                "success": False,
                "message": f"HTTP {response.status_code}",
                "status_code": response.status_code,
                "request_headers": request_headers,
                "request_payload": payload,
            }

        # 5xx / other — raise so the Celery task retries.
        response.raise_for_status()

        body = response.json()
        if not body.get("success"):
            logger.warning(
                "[VFS] Postback returned success=false for order %s: %s",
                order.source_order_id,
                body,
            )
            return {
                "success": False,
                "message": body.get("errorMsg", "VFS returned success=false"),
                "error_code": body.get("errorCode"),
                "request_headers": request_headers,
                "request_payload": payload,
            }

        logger.info("[VFS] Postback delivered for order %s", order.source_order_id)
        return {
            "success": True,
            "status_code": response.status_code,
            "request_headers": request_headers,
            "request_payload": payload,
        }


vfs_service = VFSService()


# ---------------------------------------------------------------------------
# Mock helpers (used when USE_MOCK is enabled)
# ---------------------------------------------------------------------------

def _mock_postback_response(
    source_order_id: Optional[str],
    event_status: str,
    request_headers: Optional[Dict[str, Any]] = None,
    request_payload: Optional[Dict[str, Any]] = None,
) -> dict:
    """Return a fake VFS postback acknowledgement for local development / testing."""
    logger.info(
        "[VFS][MOCK] Postback acknowledged for order %s (status=%s)",
        source_order_id,
        event_status,
    )
    return {
        "success": True,
        "status_code": 200,
        "mock": True,
        "request_headers": request_headers,
        "request_payload": request_payload,
    }
