"""VFS service for sending order status postback webhooks (SiteFlow style)."""
import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import httpx

from app.core.config import settings
from app.models.order import Order

logger = logging.getLogger(__name__)
USE_MOCK = True


class VFSService:
    """Service for pushing order status postbacks to the VFS webhook endpoint."""

    def __init__(self):
        self.postback_url = settings.VFS_POSTBACK_URL.rstrip("/") if settings.VFS_POSTBACK_URL else ""

    def send_status_postback(
        self,
        order: Order,
        event_status: str,
    ) -> Dict[str, Any]:
        """
        POST an order status postback webhook to VFS.

        Payload follows the Site Flow postback shape defined in the status
        sync spec: ``{timestamp, sourceOrderId, status}``. Shipment/tracking
        detail and item quantity are intentionally not included here — the
        real Site Flow postback template is customer-configured per account,
        and the exact shipped-event shape VFS wants is still unconfirmed
        (see `docs/siteflow-postback-research.md`).

        Authentication headers (per the status sync spec):
        - ``X-SITEFLOW-NONCE``: random unique token per request
        - ``X-SITEFLOW-SIGNATURE``: ``{partnerId}:{hex(HMAC-SHA256(nonce, partnerApiKey))}``

        Args:
            order: The order model instance (uses ``order.source_order_id``).
            event_status: External status code (e.g. ``printed``, ``shipped``).

        Returns:
            ``{"success": True/False, ...}``.  Non-retryable failures (4xx,
            unconfigured URL) return ``success=False``; transient failures
            (5xx, network) raise so the caller can retry.
        """
        if not self.postback_url:
            logger.warning("[VFS] VFS_POSTBACK_URL is not configured – skipping postback.")
            return {"success": False, "message": "VFS_POSTBACK_URL not configured"}

        payload: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "sourceOrderId": order.source_order_id,
            "status": event_status,
        }

        if USE_MOCK:
            return _mock_postback_response(order.source_order_id, event_status)

        headers = self._build_auth_headers()

        logger.info(
            "[VFS] POST postback status=%s sourceOrderId=%s",
            event_status,
            order.source_order_id,
        )

        with httpx.Client(timeout=15.0, follow_redirects=True) as client:
            response = client.post(self.postback_url, json=payload, headers=headers)

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
            }

        # 5xx / other — raise so the Celery task retries.
        response.raise_for_status()

        logger.info("[VFS] Postback delivered for order %s", order.source_order_id)
        return {"success": True, "status_code": response.status_code}

    @staticmethod
    def _build_auth_headers() -> Dict[str, str]:
        """Build the SiteFlow-style nonce + signature headers."""
        nonce = uuid.uuid4().hex
        signature = hmac.new(
            settings.VFS_PARTNER_API_KEY.encode("utf-8"),
            nonce.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-SITEFLOW-NONCE": nonce,
            "X-SITEFLOW-SIGNATURE": f"{settings.VFS_PARTNER_ID}:{signature}",
        }


vfs_service = VFSService()


# ---------------------------------------------------------------------------
# Mock helpers (used when USE_MOCK is enabled)
# ---------------------------------------------------------------------------

def _mock_postback_response(source_order_id: Optional[str], event_status: str) -> dict:
    """Return a fake VFS postback acknowledgement for local development / testing."""
    logger.info(
        "[VFS][MOCK] Postback acknowledged for order %s (status=%s)",
        source_order_id,
        event_status,
    )
    return {"success": True, "status_code": 200, "mock": True}
