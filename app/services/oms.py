"""OMS service for fetching order delivery/billing addresses."""
import logging
import httpx
from typing import Optional
from sqlmodel import Session, select

from app.core.config import settings
from app.models.address import Address, AddressType

logger = logging.getLogger(__name__)
USE_MOCK = True

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

        # Use mock data when DEBUG is enabled
        if USE_MOCK:
            body = _mock_response(order_id)
        else:
            url = f"{self.base_url}/order/addresses"
            payload = {"orderNo": order_id}

            with httpx.Client(timeout=15.0, follow_redirects=True) as client:
                response = client.post(url, json=payload)
                response.raise_for_status()
            body = response.json()

        result = {"delivery": None, "billing": None}
        print(body)
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
                "state": "Zhejiang",
                "city": "Hangzhou",
                "address1": "Xixi Shouzuo, Building A, Room 1001",
                "address2": "Floor 10",
                "postcode": "310000",
                "firstName": "San",
                "lastName": "Zhang",
                "phone": "0571-88888888",
                "mobile": "13800138000",
                "email": "zhangsan@example.com",
                "company": "ABC Trading Ltd.",
            },
            "billingAddress": {
                "country": "CN",
                "state": "Zhejiang",
                "city": "Hangzhou",
                "address1": "Xixi Shouzuo, Building B, Room 2001",
                "address2": "Floor 20",
                "postcode": "310000",
                "firstName": "Si",
                "lastName": "Li",
                "phone": "0571-88888888",
                "mobile": "13900139000",
                "email": "lisi@example.com",
                "company": "ABC Trading Ltd.",
            },
        },
    }
