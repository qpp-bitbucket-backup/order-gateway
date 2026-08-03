"""OMS service for fetching order delivery/billing addresses."""
import json
import logging
from datetime import datetime, timezone
import httpx
from typing import Optional, List, Dict, Any
from sqlmodel import Session, select

from app.core.config import settings
from app.core import hub4
from app.models.address import Address, AddressType
from app.models.order import Order

logger = logging.getLogger(__name__)
USE_MOCK = True


class OMSRetryableError(Exception):
    """Raised when OMS API returns a retryable error (503 / timeout)."""
    pass

class OMSService:
    """Service for interacting with OMS API to retrieve address information."""

    def __init__(self):
        self.base_url = settings.OMS_API_URL.rstrip("/") if settings.OMS_API_URL else ""

    def fetch_order_addresses(
        self,
        order_id: str,
        session: Session,
    ) -> dict:
        """
        Call OMS API to get delivery and billing addresses for an order,
        then persist them into the addresses table.

        Args:
            order_id: The order ID to look up in OMS.
            session: Active database session.

        Returns:
            A dict with keys ``delivery`` and ``billing``, each containing
            the corresponding ``Address`` model instance (or ``None``).
        """
        if not self.base_url:
            logger.warning("[OMS] OMS_API_URL is not configured – skipping address fetch.")
            return {"delivery": None, "billing": None}

        payload = {"orderNo": order_id}

        # Use mock data when DEBUG is enabled
        if USE_MOCK:
            body = _mock_response(order_id)
        else:
            app_secret = settings.OMS_APP_SECRET
            body_ciphertext = hub4.aes_encrypt(
                json.dumps(payload, ensure_ascii=False),
                app_secret,
            )

            params = hub4.build_query_params(
                method_name=settings.OMS_STATUS_METHOD_NAME,
                source_app=settings.OMS_SOURCE_APP,
                interface_type=settings.OMS_INTERFACE_TYPE,
            )
            sign = hub4.make_sign(params, body_ciphertext, app_secret)

            # HUB4 spec: URL uses capital "Version", sign uses lowercase "version".
            url_params: Dict[str, str] = {}
            for key, value in params.items():
                url_params["Version" if key == "version" else key] = value
            url_params["sign"] = sign

            logger.info("[OMS] POST API-001 status=%s orderNo=%s", event_status, order.order_id)


            try:
                with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                    response = client.post(
                        f"{self.base_url}/order/addresses",
                        params=url_params,
                        content=body_ciphertext,
                        headers={"Content-Type": "text/plain"},
                    )
            except httpx.TimeoutException:
                logger.warning("[OMS] API-001 timeout for order %s", order_id)
                raise OMSRetryableError(f"OMS API timeout for order {order_id}")

            if response.status_code == 503:
                logger.warning("[OMS] API-001 returned 503 for order %s", order_id)
                raise OMSRetryableError(f"OMS API returned 503 for order {order_id}")

            response.raise_for_status()
            body = response.json()

        result = {"delivery": None, "billing": None}
        try:
            if not body.get("success"):
                logger.warning("[OMS] API returned success=false for order %s: %s", order_id, body)
                return result

            data = body.get("data", {})

            # Remove existing addresses for this order to avoid duplicates
            existing = session.exec(
                select(Address).where(Address.order_id == order_id)
            ).all()
            for addr in existing:
                session.delete(addr)

            # Persist delivery address
            delivery_data = data.get("deliveryAddress")
            if delivery_data:
                delivery_addr = self._map_to_address(delivery_data, order_id, AddressType.DELIVERY)
                session.add(delivery_addr)
                result["delivery"] = delivery_addr

            # Persist billing address
            billing_data = data.get("billingAddress")
            if billing_data:
                billing_addr = self._map_to_address(billing_data, order_id, AddressType.BILLING)
                session.add(billing_addr)
                result["billing"] = billing_addr

            session.commit()
            logger.info("[OMS] Addresses saved for order %s", order_id)

        except Exception as exc:
            logger.error("[OMS] Failed to fetch addresses for order %s: %s", order_id, exc)

        return result

    def update_order_status(
        self,
        order: Order,
        event_status: str,
        status_desc: Optional[str] = None,
        shipments: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """
        Call OMS API-002 to notify an order status update.

        Builds the HUB4-encrypted business payload, signs it, POSTs to the
        OMS endpoint and decrypts the response.

        Args:
            order: The order model instance (uses ``order.order_id`` as orderNo).
            event_status: External status code (e.g. ``printed``, ``shipped``).
            status_desc: Human-readable status description (defaults to event_status).
            shipments: Optional list of shipment dicts (trackingNumber/carrierName/shipDate).

        Returns:
            ``{"success": True/False, ...}``.  Non-retryable failures (4xx,
            business errors, unconfigured URL) return ``success=False``;
            transient failures (5xx, network) raise so the caller can retry.
        """
        if not self.base_url:
            logger.warning("[OMS] OMS_API_URL is not configured – skipping status update.")
            return {"success": False, "message": "OMS_API_URL not configured"}

        payload = {
            "orderNo": order.order_id,
            "status": event_status,
            "statusDesc": status_desc or event_status,
            "timestamp": int(datetime.now(timezone.utc).timestamp() * 1000),
            "shipments": shipments or [],
        }

        if USE_MOCK:
            mock_params = hub4.build_query_params(
                method_name=settings.OMS_STATUS_METHOD_NAME,
                source_app=settings.OMS_SOURCE_APP,
                interface_type=settings.OMS_INTERFACE_TYPE,
            )
            mock_headers = {
                "Content-Type": "text/plain",
                **{"Version" if k == "version" else k: v for k, v in mock_params.items()},
                "sign": "mock-signature",
            }
            return _mock_status_response(order.order_id, event_status, mock_headers, payload)

        app_secret = settings.OMS_APP_SECRET
        body_ciphertext = hub4.aes_encrypt(
            json.dumps(payload, ensure_ascii=False),
            app_secret,
        )

        params = hub4.build_query_params(
            method_name=settings.OMS_STATUS_METHOD_NAME,
            source_app=settings.OMS_SOURCE_APP,
            interface_type=settings.OMS_INTERFACE_TYPE,
        )
        sign = hub4.make_sign(params, body_ciphertext, app_secret)

        # HUB4 spec: URL uses capital "Version", sign uses lowercase "version".
        url_params: Dict[str, str] = {}
        for key, value in params.items():
            url_params["Version" if key == "version" else key] = value
        url_params["sign"] = sign

        logger.info("[OMS] POST API-002 status=%s orderNo=%s", event_status, order.order_id)

        request_headers = {"Content-Type": "text/plain", **url_params}

        with httpx.Client(timeout=30.0, follow_redirects=True) as client:
            response = client.post(
                self.base_url,
                params=url_params,
                content=body_ciphertext,
                headers={"Content-Type": "text/plain"},
            )

        # 4xx — business error, do not retry.
        if 400 <= response.status_code < 500:
            logger.warning(
                "[OMS] API-002 returned %s for order %s: %s",
                response.status_code,
                order.order_id,
                response.text,
            )
            return {
                "success": False,
                "message": f"HTTP {response.status_code}",
                "status_code": response.status_code,
                "request_headers": request_headers,
                "request_payload": payload,
            }

        # 5xx / network — raise so the Celery task can retry.
        response.raise_for_status()

        decrypted = hub4.aes_decrypt(response.text, app_secret)
        body = json.loads(decrypted)

        if not body.get("success"):
            logger.warning(
                "[OMS] API-002 returned success=false for order %s: %s",
                order.order_id,
                body,
            )
            return {
                "success": False,
                "message": body.get("message", "OMS returned success=false"),
                "request_headers": request_headers,
                "request_payload": payload,
            }

        logger.info("[OMS] Status update acknowledged for order %s -> %s", order.order_id, event_status)
        return {
            "success": True,
            "data": body.get("data"),
            "request_headers": request_headers,
            "request_payload": payload,
        }

    @staticmethod
    def _map_to_address(data: dict, order_id: str, address_type: AddressType) -> Address:
        """Map an OMS API address dict to an Address model."""
        return Address(
            country=data.get("country"),
            state=data.get("state"),
            city=data.get("city"),
            address1=data.get("address1"),
            address2=data.get("address2"),
            postcode=data.get("postcode"),
            first_name=data.get("firstName"),
            last_name=data.get("lastName"),
            phone=data.get("phone"),
            mobile=data.get("mobile"),
            email=data.get("email"),
            company=data.get("company"),
            order_id=order_id,
            type=address_type,
        )


oms_service = OMSService()


# ---------------------------------------------------------------------------
# Mock helpers (used when DEBUG=True)
# ---------------------------------------------------------------------------

def _mock_response(order_id: str) -> dict:
    """Return a fake OMS API response for local development / testing."""
    logger.info("[OMS][MOCK] Returning mock addresses for order %s", order_id)
    return {
        "success": True,
        "data": {
            "deliveryAddress": {
                "country": "CN",
                "state": "Guangdong",
                "city": "Dongguan",
                "address1": "Dongshan Industrial District, Aobeiwei, Zhangmutou",
                "address2": "",
                "postcode": "523619",
                "firstName": "Topps",
                "lastName": "Now",
                "phone": "86-88888888",
                "mobile": "1888888888",
                "email": "ivanliyp@qpp.com",
                "company": "Q P Group Holdings Limited.",
            },
            "billingAddress": {
                "country": "CN",
                "state": "Guangdong",
                "city": "Dongguan",
                "address1": "Dongshan Industrial District, Aobeiwei, Zhangmutou",
                "address2": "",
                "postcode": "523619",
                "firstName": "Topps",
                "lastName": "Now",
                "phone": "86-88888888",
                "mobile": "1888888888",
                "email": "ivanliyp@qpp.com",
                "company": "Q P Group Holdings Limited.",
            },
        },
    }


def _mock_status_response(
    order_id: str,
    event_status: str,
    request_headers: Optional[Dict[str, Any]] = None,
    request_payload: Optional[Dict[str, Any]] = None,
) -> dict:
    """Return a fake OMS API-002 response for local development / testing."""
    logger.info("[OMS][MOCK] Returning mock status update for order %s -> %s", order_id, event_status)
    return {
        "success": True,
        "data": {
            "orderNo": order_id,
            "status": event_status,
        },
        "request_headers": request_headers,
        "request_payload": request_payload,
    }
