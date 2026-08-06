"""VFS service for sending order status postback webhooks (SiteFlow style)."""
import json
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import httpx

from app.core.config import settings
from app.core import hub4
from app.models.order import Order

logger = logging.getLogger(__name__)
USE_MOCK = False

# HUB4 methodName for VFS postback calls. The mock doesn't actually validate
# this value (confirmed empirically — any methodName is accepted as long as
# the signature is valid), but keep it distinct from OMS's for log clarity.
VFS_METHOD_NAME = "vfs_status_postback"


class VFSService:
    """Service for pushing order status postbacks to a per-order VFS webhook endpoint."""

    def send_status_postback(
        self,
        order: Order,
        event_status: str,
        shipments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        POST an order status postback webhook to VFS.

        Per VFS/Ivan's 2026-08 decision, the postback destination is supplied
        per-order at order-creation time (``orderData.postbackAddress``, read
        from ``order.order_data``) rather than a single system-level URL —
        this deliberately diverges from HP Site Flow's own documented
        behavior (see docs/system-design-and-postback-investigation.md §8.1,
        which found real Site Flow does NOT support per-order postback URLs).
        There is no fallback: an order with no ``postbackAddress`` is skipped.

        Authentication: HUB4 (same AES-encrypted-body + signed-query-params
        scheme as OMS API-002), confirmed empirically against Ivan's mock —
        it turned out to be the same underlying endpoint as OMS
        (``order-uat.popprint.cn/mock/api/order/status``), which requires
        HUB4 auth for any call regardless of methodName. Reuses OMS's app
        secret/source app/interface type until VFS gets its own credentials.

        Args:
            order: The order model instance (uses ``order.order_data["postbackAddress"]``).
            event_status: External status code (e.g. ``printed``, ``shipped``).
            shipments: Optional list of shipment dicts (trackingNumber/carrierName/
                service/trackingUrl/shipDate), included when status is ``shipped``.

        Returns:
            ``{"success": True/False, ...}``.  Non-retryable failures (4xx,
            missing postbackAddress, business error) return ``success=False``;
            transient failures (5xx, network) raise so the caller can retry.
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

        app_secret = settings.OMS_APP_SECRET
        body_ciphertext = hub4.aes_encrypt(json.dumps(payload, ensure_ascii=False), app_secret)

        params = hub4.build_query_params(
            method_name=VFS_METHOD_NAME,
            source_app=settings.OMS_SOURCE_APP,
            interface_type=settings.OMS_INTERFACE_TYPE,
        )
        sign = hub4.make_sign(params, body_ciphertext, app_secret)

        # HUB4 spec: URL uses capital "Version", sign uses lowercase "version".
        url_params: Dict[str, str] = {}
        for key, value in params.items():
            url_params["Version" if key == "version" else key] = value
        url_params["sign"] = sign

        logger.info(
            "[VFS] POST postback status=%s sourceOrderId=%s url=%s",
            event_status,
            order.source_order_id,
            postback_url,
        )

        request_headers = {"Content-Type": "text/plain", **url_params}

        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            response = client.post(
                postback_url,
                params=url_params,
                content=body_ciphertext,
                headers={"Content-Type": "text/plain"},
            )

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
