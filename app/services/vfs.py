"""VFS service for sending order status postback webhooks (SiteFlow style)."""
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import httpx

from app.models.order import Order, OrderStatus

logger = logging.getLogger(__name__)
USE_MOCK = False


class VFSService:
    """Service for pushing order status postbacks to a per-order VFS webhook endpoint."""

    @staticmethod
    def _build_shipped_payload(order: Order, shipments: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
        """Build the "Order Shipped" postback body per
        docs/siteflow_data_examples_r1.md §Postbacks (provided by Dan/VFS) —
        a distinct shape from the generic status postback used for every
        other status.

        ``orderId`` here is VFS's own order id, i.e. ``order.source_order_id``
        — ``order.order_id`` is our internal id (``_id`` in VFS's own
        vocabulary), which VFS has no use for in an inbound postback.

        ``shippedDate``/``trackingNumber``/``trackingUrl`` come from the
        inbound QPMN ``package_shipped`` event (``shipments[0]``, QPMN's own
        shape — see ``QpmnWebhookShipment``).

        ``carrier`` is a best-effort partial: VFS doesn't submit carrier info
        at order creation (that's QPMN's responsibility end-to-end), and
        QPMN's ``package_shipped`` event only gives a flat "company" string,
        not the structured code/service/serviceId/alias breakdown VFS's full
        example shows. We map "company" onto ``carrier.code`` (closest
        semantic match — the doc describes ``code`` as e.g. "fedex").
        ``service`` is hardcoded to ``"Standard"`` — every store we push to
        QPMN currently defaults to Standard shipping (no per-order signal
        exists to say otherwise; see ``fetch_shipping_method_from_qpmn``),
        so this avoids an extra QPMN API call on every shipped postback.
        ``serviceId``/``alias`` stay ``null``, rather than fabricate data
        neither QPMN nor VFS gives us. Revisit if that ever stops holding.
        """
        shipment = (shipments or [{}])[0] or {}
        ship_date_ms = shipment.get("shipDate")
        shipped_date = None
        if ship_date_ms is not None:
            shipped_date = (
                datetime.fromtimestamp(ship_date_ms / 1000, tz=timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
            )

        return {
            "orderId": order.source_order_id,
            "shippedDate": shipped_date,
            "trackingNumber": shipment.get("trackingNumber"),
            "trackingUrl": shipment.get("trackingUrl"),
            "status": OrderStatus.SHIPPED.value,
            "carrier": {
                "code": shipment.get("company"),
                "service": "Standard",
                "serviceId": None,
                "alias": None,
            },
        }

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

        if event_status == OrderStatus.SHIPPED.value:
            payload = self._build_shipped_payload(order, shipments)
        else:
            payload: Dict[str, Any] = {
                "orderId": order.source_order_id,
                "status": event_status,
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
            try:
                error_body = response.json()
            except ValueError:
                error_body = {}
            return {
                "success": False,
                "message": error_body.get("errorMsg", f"HTTP {response.status_code}"),
                "error_code": error_body.get("errorCode"),
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
            "response": body,
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
