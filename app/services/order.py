"""Order service for managing order-related operations."""
import logging
import os
import re
import tempfile
import uuid
import fitz  # PyMuPDF
import base64
import httpx
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

from sqlmodel import Session, select
from sqlalchemy import or_
from app.models.product import Sku

from app.models.order import Order, OrderStatus, OrderType
from app.models.address import Address, AddressType
from app.tasks.orders import publish_order
from app.services.file import file_service
from app.services.client import client_service
from app.services.address_mapping import get_state_code, to_iso_country_code
from app.core.config import settings
from app.core.sentry_alerts import ALERTS, capture_integration_alert

logger = logging.getLogger(__name__)

_CANCEL_ALERTS = ALERTS["cancel"]
_SHIPPING_ALERTS = ALERTS["shipping"]
_CURRENCY_ALERTS = ALERTS["currency"]


def _capture_cancel_alert(alert_key: str, order_id: Optional[str] = None, **format_args) -> None:
    """Capture a cancel_qpmn_order failure to Sentry (synchronous, non-Celery)."""
    capture_integration_alert(
        _CANCEL_ALERTS[alert_key], order_id=order_id,
        format_args={"order_id": order_id, **format_args}, component="qpmn_api",
    )


def _capture_shipping_alert(alert_key: str, **format_args) -> None:
    """Capture a fetch_shipping_method_from_qpmn failure to Sentry."""
    capture_integration_alert(
        _SHIPPING_ALERTS[alert_key], format_args=format_args, component="qpmn_api",
    )


def _capture_currency_alert(alert_key: str, **format_args) -> None:
    """Capture a fetch_currency_from_qpmn failure to Sentry."""
    capture_integration_alert(
        _CURRENCY_ALERTS[alert_key], format_args=format_args, component="qpmn_api",
    )


# Statuses that allow updates (order has not reached print-ready stage)
UPDATABLE_STATUSES = {
    OrderStatus.RECEIVED,
    OrderStatus.PENDING,
    OrderStatus.VALIDATED,
    OrderStatus.PROCESSING,
    OrderStatus.FAILED,
    OrderStatus.ERRORED,
}

# Statuses that do NOT allow cancellation
NON_CANCELLABLE_STATUSES = {
    OrderStatus.PRINTREADY,
    OrderStatus.PRINTED,
    OrderStatus.SHIPPED,
    OrderStatus.CANCELLED,
}


class OrderNotCancellableError(ValueError):
    """Raised when the order status does not allow cancellation (HTTP 409)."""
    pass


class QPMNCancelError(ValueError):
    """Raised when the QPMN cancel API call fails (HTTP 502)."""
    def __init__(self, message: str, status_code: Optional[int] = None, error: Any = None):
        super().__init__(message)
        self.qpmn_status_code = status_code
        self.qpmn_error = error


# Parallel-card sourceOrderId format: "aaaaaaa[-_]b_Sccccc" where "aaaaaaa" is
# the associated base card order's sourceOrderId, "b" the version number and
# "ccccc" the shipping number. The version separator is a hyphen or an
# underscore (both occur in practice):
#   "CN-TEST20260811-1_S1055552"  -> base "CN-TEST20260811"
#   "IVAN-TEST-0004_1_S10098923"  -> base "IVAN-TEST-0004"
_PARALLEL_CARD_RE = re.compile(r"^(.+)[-_](\d+)_S(.+)$")


def parse_parallel_card_id(source_order_id: str) -> Optional[Tuple[str, str, str]]:
    """
    Parse a parallel-card ``source_order_id`` (``aaaaaaa[-_]b_Sccccc``).

    Returns ``(base_source_order_id, version, shipping_number)``, or ``None``
    when the id does not follow the format — ``_S`` must sit between a
    ``-``/``_`` + version digits suffix and a non-empty shipping number.
    """
    match = _PARALLEL_CARD_RE.match(source_order_id)
    if not match:
        return None
    return match.group(1), match.group(2), match.group(3)


class OrderService:
    """Service for handling order-related operations."""

    # ------------------------------------------------------------------
    # Read helpers
    # ------------------------------------------------------------------

    def get_all_orders(
        self,
        session: Session,
        store_id: Optional[str] = None,
        page: int = 1,
        pagesize: int = 10,
        statuses: Optional[List[OrderStatus]] = None,
        source_order_id: Optional[str] = None,
    ) -> Tuple[List[Order], int, int]:
        """
        Retrieve a paginated list of orders.

        Args:
            statuses: Optional list of order statuses to filter by (IN clause).
            source_order_id: Optional fuzzy match on source_order_id (LIKE %text%).

        Returns:
            A tuple of (orders, total_count, total_pages).
        """
        query = select(Order)
        if store_id:
            query = query.where(Order.store_id == store_id)
        if statuses:
            query = query.where(Order.status.in_(statuses))
        if source_order_id:
            query = query.where(Order.source_order_id.like(f"%{source_order_id}%"))

        # Total count
        total_count = len(session.exec(query).all())
        total_pages = (total_count + pagesize - 1) // pagesize

        # Paginated results
        offset = (page - 1) * pagesize
        statement = (
            query
            .offset(offset)
            .limit(pagesize)
            .order_by(Order.created_at.desc())
        )
        orders = session.exec(statement).all()

        return orders, total_count, total_pages

    def get_order_by_id(
        self,
        session: Session,
        order_id: str,
        store_id: Optional[str] = None,
    ) -> Optional[Order]:
        """
        Retrieve a single order by its internal order_id.

        Returns the Order if found (and optionally scoped to *store_id*),
        otherwise ``None``.
        """
        query = select(Order).where(Order.order_id == order_id)
        if store_id:
            query = query.where(Order.store_id == store_id)
        return session.exec(query).first()

    # ------------------------------------------------------------------
    # Write helpers
    # ------------------------------------------------------------------

    def check_duplicate(
        self,
        session: Session,
        source_order_id: str,
        store_id: Optional[str] = None,
    ) -> Optional[Order]:
        """
        Check whether an order with the given *source_order_id* already exists
        (optionally scoped to *store_id*).

        Orders in ``CANCELLED`` or ``ERRORED`` status are excluded — those
        statuses free up the ``source_order_id`` for reuse (e.g. the
        artwork-update flow cancels an order and immediately re-creates one
        with the same ``source_order_id``).

        Returns the existing Order if found, otherwise ``None``.
        """
        query = select(Order).where(
            Order.source_order_id == source_order_id,
            Order.status.not_in([OrderStatus.CANCELLED, OrderStatus.ERRORED]),
        )
        if store_id:
            query = query.where(Order.store_id == store_id)
        return session.exec(query).first()

    def find_parallel_card_parent(
        self,
        session: Session,
        source_order_id: str,
        store_id: Optional[str] = None,
    ) -> Optional[Order]:
        """
        Detect a parallel-card order.

        A parallel-card ``source_order_id`` follows the format
        ``aaaaaaa[-_]b_Sccccc`` (e.g. ``CN-TEST20260811-1_S1055552`` or
        ``IVAN-TEST-0004_1_S10098923``): only the ``aaaaaaa`` part (without
        the version suffix) is the associated base card order's
        ``source_order_id``. When that base card order exists (optionally
        scoped to *store_id*), the incoming order is a parallel card of it.

        Returns the matched base card Order, otherwise ``None``.
        """
        parsed = parse_parallel_card_id(source_order_id)
        if not parsed:
            return None
        base_id = parsed[0]
        query = select(Order).where(Order.source_order_id == base_id)
        if store_id:
            query = query.where(Order.store_id == store_id)
        return session.exec(query).first()

    def create_order(
        self,
        session: Session,
        *,
        source_account: str,
        source_order_id: str,
        destination: Dict[str, Any],
        order_data: Dict[str, Any],
        store_id: Optional[str] = None,
        order_type: OrderType = OrderType.BASE_CARD,
    ) -> Order:
        """
        Create a new order, persist it, and enqueue the Celery publish task.

        Returns the newly created Order.
        """
        order_id = str(uuid.uuid4())

        order = Order(
            order_id=order_id,
            source_account=source_account,
            source_order_id=source_order_id,
            destination=destination,
            source={
                "name": source_account,
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            },
            order_data=order_data,
            status=OrderStatus.RECEIVED,
            version=1,
            store_id=store_id,
            type=order_type,
        )

        session.add(order)
        session.commit()
        session.refresh(order)

        # Enqueue Celery task
        task_payload = {
            "order_id": order.order_id,
            "source_order_id": order.source_order_id,
            "status": order.status.value,
            "created_at": order.created_at.isoformat() if order.created_at else None,
        }
        publish_order.apply_async(args=[task_payload])

        logger.info("[OrderService] Created order %s (source=%s)", order_id, source_order_id)
        return order

    def update_order(
        self,
        session: Session,
        order: Order,
        *,
        destination: Optional[Dict[str, Any]] = None,
        order_data: Optional[Dict[str, Any]] = None,
    ) -> Tuple[Order, List[str]]:
        """
        Update an existing order's destination and/or order_data.

        The order must be in an *updatable* status.

        Returns:
            A tuple of (updated Order, list of changed field names).

        Raises:
            ValueError: If the order status does not allow updates.
        """
        if order.status not in UPDATABLE_STATUSES:
            raise ValueError(
                f"Cannot update order with status '{order.status.value}'."
            )

        changes: List[str] = []

        if destination is not None:
            order.destination = destination
            changes.append("destination")

        if order_data is not None:
            order.order_data = order_data
            changes.append("orderData")

        if not changes:
            raise ValueError("At least one of 'destination' or 'orderData' must be provided.")

        # Bump version and record log
        order.version += 1
        log_entry = {
            "action": "updated",
            "fields": changes,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        order.logs = (order.logs or []) + [log_entry]

        session.add(order)
        session.commit()
        session.refresh(order)

        logger.info("[OrderService] Updated order %s fields: %s", order.order_id, changes)
        return order, changes

    def cancel_qpmn_order(self, order: Order) -> Dict[str, Any]:
        """
        Call QPMN cancel API to cancel the order on the QPMN side.

        PUT {QPMN_OPEN_API_URL}/orders/{store_order_id}/cancel
        Authorization: Basic {store_token}

        Returns:
            A dict with keys:
            - ``success``: True only when QPMN returns HTTP 200 with ``success=true``.
            - ``status_code``: HTTP status code from QPMN.
            - ``data``: Response body data (on success).
            - ``error``: Error message (on failure).
        """
        # No store_order_id means the order was never pushed to QPMN,
        # so there is nothing to cancel on the QPMN side — treat as success.
        if not order.store_order_id:
            logger.info("[OrderService] Order %s has no store_order_id (never pushed to QPMN), skip cancel API call", order.order_id)
            return {"success": True, "status_code": None, "data": None, "skipped": True}

        store_key = client_service.get_store_key_by_id(order.store_id) if order.store_id else None
        if not store_key:
            logger.warning("[OrderService] No store_key for order %s, cannot call QPMN cancel API", order.order_id)
            _capture_cancel_alert("NO_STORE_KEY", order.order_id, store_id=order.store_id)
            return {"success": False, "status_code": None, "error": "No store_key configured"}

        api_url = f"{settings.QPMN_OPEN_API_URL}/orders/{order.store_order_id}/cancel"
        headers = {
            "Authorization": f"Basic {store_key}",
            "Content-Type": "application/json",
        }

        logger.info(
            "[OrderService] Calling QPMN cancel API for order %s (store_order_id=%s)",
            order.order_id,
            order.store_order_id,
        )

        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                response = client.put(api_url, headers=headers)
        except httpx.TimeoutException:
            logger.error("[OrderService] QPMN cancel API timeout for order %s", order.order_id)
            _capture_cancel_alert("TIMEOUT", order.order_id)
            return {"success": False, "status_code": None, "error": "QPMN API timeout"}
        except Exception as exc:
            logger.error("[OrderService] QPMN cancel API request failed for order %s: %s", order.order_id, exc)
            _capture_cancel_alert("REQUEST_FAILED", order.order_id, exc=exc)
            return {"success": False, "status_code": None, "error": str(exc)}
        # Non-200 responses are failures
        if response.status_code != 200:
            error_body = None
            try:
                error_body = response.json()
            except Exception:
                error_body = response.text
            logger.warning(
                "[OrderService] QPMN cancel API returned %s for order %s: %s",
                response.status_code,
                order.order_id,
                error_body,
            )
            _capture_cancel_alert("REJECTED_HTTP", order.order_id, status_code=response.status_code, error_body=error_body)
            return {
                "success": False,
                "status_code": response.status_code,
                "error": error_body,
            }

        body = response.json()
        if not body.get("success"):
            logger.warning(
                "[OrderService] QPMN cancel API returned success=false for order %s: %s",
                order.order_id,
                body,
            )
            _capture_cancel_alert("REJECTED_SUCCESS_FALSE", order.order_id, body=body)
            return {
                "success": False,
                "status_code": 200,
                "error": body,
            }

        logger.info("[OrderService] QPMN cancel API succeeded for order %s", order.order_id)
        return {
            "success": True,
            "status_code": 200,
            "data": body.get("data"),
        }

    def cancel_order(
        self,
        session: Session,
        order: Order,
    ) -> Order:
        """
        Cancel an existing order.

        Calls the QPMN cancel API first. Only when QPMN returns HTTP 200 with
        ``success=true`` does the local order status change to CANCELLED.
        All results (request, response, success/failure) are appended to
        ``order.logs``.

        Raises:
            ValueError: If the order status does not allow cancellation, or
                if the QPMN cancel API call fails.
        """
        if order.status in NON_CANCELLABLE_STATUSES:
            order.logs = (order.logs or []) + [
                {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "action": "cancel_rejected",
                    "message": (
                        f"Cannot cancel order with status '{order.status.value}'."
                    ),
                },
            ]
            session.add(order)
            session.commit()
            session.refresh(order)
            raise OrderNotCancellableError(
                f"Cannot cancel order with status '{order.status.value}'."
            )

        # --- 1. Call QPMN cancel API ---
        qpmn_result = self.cancel_qpmn_order(order)

        now_iso = datetime.now(timezone.utc).isoformat()

        if qpmn_result.get("success"):
            # --- QPMN confirmed cancellation ---
            order.status = OrderStatus.CANCELLED
            order.logs = (order.logs or []) + [
                {
                    "timestamp": now_iso,
                    "action": "order_cancelled",
                    "message": "Order status updated to cancelled",
                },
            ]
            session.add(order)
            session.commit()
            session.refresh(order)

            logger.info("[OrderService] Cancelled order %s", order.order_id)
            return order

        # --- QPMN cancellation failed ---
        error_detail = qpmn_result.get("error", "Unknown error")
        qpmn_status_code = qpmn_result.get("status_code")
        order.logs = (order.logs or []) + [
            {
                "timestamp": now_iso,
                "action": "qpmn_cancel_failed",
                "message": (
                    f"QPMN cancel API failed (HTTP {qpmn_status_code}): "
                    f"{error_detail}."
                ),
                "status_code": qpmn_status_code,
                "error": error_detail,
                "response": qpmn_result.get("data"),
            },
        ]
        session.add(order)
        session.commit()
        session.refresh(order)

        raise QPMNCancelError(
            f"QPMN cancel API failed (HTTP {qpmn_status_code}): {error_detail}.",
            status_code=qpmn_status_code,
            error=error_detail,
        )

    # ------------------------------------------------------------------
    # Address sync (OMS fetch → compare → QPMN update)
    # ------------------------------------------------------------------

    _ADDRESS_COMPARE_FIELDS = (
        "country", "state", "city", "address1", "address2",
        "postcode", "first_name", "last_name",
        "phone", "mobile", "email", "company",
    )

    @staticmethod
    def _address_signature(addr: Optional[Address]) -> Optional[tuple]:
        """Build a hashable signature from an Address for comparison."""
        if addr is None:
            return None
        return tuple(
            (getattr(addr, f) or "") for f in OrderService._ADDRESS_COMPARE_FIELDS
        )

    def sync_order_address(self, session: Session, order: Order) -> Dict[str, Any]:
        """
        Fetch the latest delivery address from OMS, compare it with the
        stored address, and—if it changed—call the QPMN update-address API.

        Returns a result dict:
        ::
            {
              "address_changed": bool,
              "qpmn_updated": Optional[bool],   # None = skipped (no store_order_id)
              "qpmn_result": Optional[dict],
            }
        """
        from app.services.oms import oms_service

        result: Dict[str, Any] = {
            "address_changed": False,
            "qpmn_updated": None,
            "qpmn_result": None,
        }

        # 1. Fetch latest addresses from OMS (persists to DB as side-effect)
        oms_result = oms_service.fetch_order_addresses(order.source_order_id, session)
        new_delivery = oms_result.get("delivery")

        # 2. Get the previously stored delivery address (before OMS overwrote it)
        #    oms_service already deleted old rows and inserted new ones,
        #    so we need to compare what we *just* got with what was there before.
        #    Re-query the latest delivery address.
        latest_delivery = session.exec(
            select(Address).where(
                (Address.order_id == order.order_id)
                & (Address.type == AddressType.DELIVERY)
            ).order_by(Address.id.desc())  # type: ignore[union-attr]
        ).first()

        # We cannot compare with the "before" state because OMS already
        # overwrote the DB.  Instead, compare the OMS-returned address with
        # the order_data shipments address (the original submitted address).
        # If OMS returned something and it differs from order_data, it changed.
        old_delivery = self._get_order_data_delivery_address(order)

        if new_delivery and self._address_signature(new_delivery) != self._address_signature(old_delivery):
            result["address_changed"] = True
            logger.info("[OrderService] Address changed for order %s", order.order_id)

            # 3. If the order has been pushed to QPMN, update the address there
            if order.store_order_id:
                qpmn_result = self.update_qpmn_address(session, order, latest_delivery or new_delivery)
                result["qpmn_result"] = qpmn_result
                result["qpmn_updated"] = qpmn_result.get("success", False)

                if not qpmn_result.get("success"):
                    # Write detailed failure info to order logs
                    order.logs = (order.logs or []) + [
                        {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "action": "qpmn_address_update_failed",
                            "message": (
                                f"QPMN address update failed (HTTP {qpmn_result.get('status_code')}): "
                                f"{qpmn_result.get('error')}."
                            ),
                            "status_code": qpmn_result.get("status_code"),
                            "error": qpmn_result.get("error"),
                        },
                    ]
                    session.add(order)
                    session.commit()
                    session.refresh(order)
                else:
                    # Write success info to order logs
                    order.logs = (order.logs or []) + [
                        {
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                            "action": "qpmn_address_update_succeeded",
                            "message": (
                                f"QPMN address update succeeded (HTTP {qpmn_result.get('status_code', 200)}): "
                                f"store_order_id={order.store_order_id}."
                            ),
                            "status_code": qpmn_result.get("status_code", 200),
                        },
                    ]
                    session.add(order)
                    session.commit()
                    session.refresh(order)
            else:
                logger.info(
                    "[OrderService] Order %s has no store_order_id, skipping QPMN address update",
                    order.order_id,
                )
        else:
            # Address unchanged — content may have changed
            # TODO: compare and update order content in QPMN
            logger.info("[OrderService] Address unchanged for order %s", order.order_id)

        return result

    @staticmethod
    def _get_order_data_delivery_address(order: Order) -> Optional[Address]:
        """Extract the delivery address from the order's stored order_data JSON."""
        order_data = order.order_data or {}
        shipments = order_data.get("shipments") or []
        if not shipments:
            return None
        ship_to = shipments[0].get("shipTo")
        if not ship_to:
            return None
        # Map shipTo fields → Address model fields
        name = ship_to.get("name", "")
        parts = name.split(" ", 1) if name else ["", ""]
        return Address(
            country=ship_to.get("isoCountry"),
            state=ship_to.get("state"),
            city=ship_to.get("town"),
            address1=ship_to.get("address1"),
            address2=ship_to.get("address2"),
            postcode=ship_to.get("postcode"),
            first_name=parts[0] if parts[0] else None,
            last_name=parts[1] if len(parts) > 1 and parts[1] else None,
            phone=ship_to.get("phone"),
            email=ship_to.get("email"),
            company=ship_to.get("companyName"),
            order_id=order.order_id,
            type=AddressType.DELIVERY,
        )

    def update_qpmn_address(self, session: Session, order: Order, addr: Address) -> Dict[str, Any]:
        """
        Call QPMN Open API to update the delivery address for an order.

        PUT {QPMN_OPEN_API_URL}/orders/{store_order_id}/deliveryAddress
        Authorization: Basic {store_key}
        """
        if not order.store_order_id:
            return {"success": False, "error": "No store_order_id"}

        store_key = client_service.get_store_key_by_id(order.store_id) if order.store_id else None
        if not store_key:
            logger.warning("[OrderService] No store_key for order %s", order.order_id)
            return {"success": False, "error": "No store_key configured"}

        api_url = (
            f"{settings.QPMN_OPEN_API_URL}/orders"
            f"/{order.store_order_id}/deliveryAddress"
        )
        headers = {
            "Authorization": f"Basic {store_key}",
            "Content-Type": "application/json",
        }
        # Country is not allowed to update in QPMN platform
        payload = {
            "state": addr.state or "",
            "city": addr.city or "",
            "address_1": addr.address1 or "",
            "address_2": addr.address2 or "",
            "postCode": addr.postcode or "",
            "firstName": addr.first_name or "",
            "lastName": addr.last_name or "",
            "phone": addr.phone or "",
            "mobile": addr.mobile or "",
            "email": addr.email or "",
        }
        # stateCode uses the same address_mapping lookup as order creation
        state_code = get_state_code(session, addr.state)
        if state_code:
            payload["stateCode"] = state_code

        logger.info(
            "[OrderService] Calling QPMN update-address API for order %s (store_order_id=%s)",
            order.order_id,
            order.store_order_id,
        )

        try:
            with httpx.Client(timeout=30.0, follow_redirects=True) as client:
                response = client.put(api_url, headers=headers, json=payload)
        except httpx.TimeoutException:
            logger.error("[OrderService] QPMN update-address timeout for order %s", order.order_id)
            return {"success": False, "error": "QPMN API timeout"}
        except Exception as exc:
            logger.error("[OrderService] QPMN update-address failed for order %s: %s", order.order_id, exc)
            return {"success": False, "error": str(exc)}

        if response.status_code != 200:
            error_body = None
            try:
                error_body = response.json()
            except Exception:
                error_body = response.text
            logger.warning(
                "[OrderService] QPMN update-address returned %s for order %s: %s",
                response.status_code,
                order.order_id,
                error_body,
            )
            return {
                "success": False,
                "status_code": response.status_code,
                "error": error_body,
            }

        body = response.json()
        if not body.get("success"):
            logger.warning(
                "[OrderService] QPMN update-address returned success=false for order %s: %s",
                order.order_id,
                body,
            )
            return {"success": False, "status_code": 200, "error": body}

        logger.info("[OrderService] QPMN update-address succeeded for order %s", order.order_id)
        return {"success": True, "status_code": 200, "data": body.get("data")}

    # ------------------------------------------------------------------
    # Order push payload builders (legacy & open API)
    # ------------------------------------------------------------------

    def build_push_payload(
        self,
        session: Session,
        order_id: str,
    ) -> Dict[str, Any]:
        """
        Dispatch to the correct payload builder based on ``QPMN_ORDER_API_VERSION``.

        Returns:
            The payload dict ready for the corresponding QPMN create-order API.
        """
        if settings.QPMN_ORDER_API_VERSION == "open":
            logger.info("[OrderService] Building Open API payload for order %s", order_id)
            return self._build_open_api_payload(session, order_id)
        logger.info("[OrderService] Building legacy API payload for order %s", order_id)
        return self._build_legacy_payload(session, order_id)

    @staticmethod
    def _split_package_quantities(total_quantity: int, max_package_quantity: int) -> List[int]:
        """
        Split a line item's quantity into packaging-sized chunks.

        Example: total_quantity=111, max_package_quantity=50 -> [50, 50, 11].
        Returns ``[total_quantity]`` unchanged when it's already within limit.
        """
        if total_quantity <= max_package_quantity:
            return [total_quantity]
        full_packages, remainder = divmod(total_quantity, max_package_quantity)
        package_quantities = [max_package_quantity] * full_packages
        if remainder:
            package_quantities.append(remainder)
        return package_quantities

    def _prepare_order_and_skus(
        self,
        session: Session,
        order_id: str,
    ) -> Tuple[Order, List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Shared helper: load order, iterate items, inject design file URLs
        into both customize structures (legacy ``designs`` pageContentDesigns
        images and Open API ``designData`` effectImages imageUrls).

        Each item is also split into one or more packaging-sized line item
        contexts when its quantity exceeds ``sku.max_package_quantity`` (see
        ``_split_package_quantities``) — the split only affects the payload
        built for QPMN, not the order's persisted ``order_data``.

        Returns:
            (order, line_item_contexts, addresses) where line_item_contexts
            is a list of dicts with keys: ``item``, ``sku``, ``files``,
            ``properties``, ``customize_project``.
        """
        order = session.exec(select(Order).where(Order.order_id == order_id)).first()
        if not order:
            raise ValueError(f"Order: [{order_id}] not found")

        files_dict = order.files or {}
        order_data = order.order_data or {}
        contexts: List[Dict[str, Any]] = []

        for item_index, item in enumerate(order_data.get("items", [])):
            sku_ref = item.get("sku")
            if not sku_ref:
                continue

            # New orders carry the third-party platform SKU (source_sku) in
            # items[].sku; legacy orders carry the internal sku_id. Resolve
            # either way — QPMN payloads always use the resolved sku.sku_id.
            sku = session.exec(
                select(Sku).where(or_(Sku.sku_id == sku_ref, Sku.source_sku == sku_ref))
            ).first()
            if not sku:
                raise ValueError(f"SKU: [{sku_ref}] not found")
            sku_id = sku.sku_id

            properties = sku.properties or {}
            # QPMN_ORDER_API_VERSION=open: the dedicated product_design_data
            # field replaces customize_project as the Open API design source
            # (falls back to customize_project for SKUs configured before the
            # field existed)
            if settings.QPMN_ORDER_API_VERSION == "open" and sku.product_design_data is not None:
                customize_project = sku.product_design_data
                designs = customize_project.get("designData", [])
            else:
                customize_project = sku.customize_project or {}
                designs = customize_project.get("designs", [])
            
            # order.files keys always use the resolved internal sku_id (the
            # publish task resolves source_sku before writing) so they match
            # the QPMN payload ids
            files = files_dict.get(f"{sku_id}-{item_index}", [])

            # Inject uploaded file URLs into pageContentDesigns images (legacy structure)
            for index, design in enumerate(designs):
                file_obj = files[index] if index < len(files) else None
                
                if not file_obj:
                    raise ValueError(f"SKU: [{sku_id}] design file not found")
                file_url = file_obj.get("url", None)
                if not file_url:
                    raise ValueError(f"SKU: [{sku_id}] design file not found")
                page_content_designs = design.get("pageContentDesigns", [])
                for pcd in page_content_designs:
                    if "image" in pcd:
                        pcd["image"] = file_url
                design["pageContentDesigns"] = page_content_designs

            # Inject uploaded file URLs into effectImages imageUrls (Open API structure):
            # designData[].views[].designs[].effectImages[].imageUrl. One file per
            # view that carries effectImages, in order; views without designs
            # (template-only materials) consume no file.
            design_data = customize_project.get("designData", [])
            open_file_index = 0
            for material in design_data:
                for view in material.get("views", []):
                    view_designs = view.get("designs") or []
                    if not any(d.get("effectImages") for d in view_designs) or open_file_index > len(files):
                        continue
                    file_obj = files[open_file_index] if open_file_index < len(files) else None
                    if not file_obj:
                        raise ValueError(f"SKU: [{sku_id}] design file not found")
                    file_url = file_obj.get("url", None)
                    if not file_url:
                        raise ValueError(f"SKU: [{sku_id}] design file URL not found")
                    open_file_index += 1
                    for d in view_designs:
                        for effect_image in d.get("effectImages", []):
                            effect_image["imageUrl"] = file_url

            # Packaging split: QPMN line items cap out at sku.max_package_quantity
            # per package, so a single VFS line item whose quantity exceeds that
            # limit becomes multiple QPMN line items — same design/SKU, just a
            # smaller quantity each (e.g. 111 @ max 50 -> 50 + 50 + 11). This is
            # push-payload-only: order.order_data keeps the original, unsplit
            # item as submitted by VFS.
            max_package_quantity = sku.max_package_quantity or settings.PACKAGE_MAX_QUANTITY_DEFAULT
            quantity = item.get("quantity", 1)
            package_quantities = self._split_package_quantities(quantity, max_package_quantity)

            for package_quantity in package_quantities:
                contexts.append({
                    "item": {**item, "quantity": package_quantity},
                    "sku": sku,
                    "files": files,
                    "properties": properties,
                    "customize_project": customize_project,
                })

        # Query addresses for this order
        delivery_address = session.exec(
            select(Address).where(
                Address.order_id == order_id,
                Address.type == AddressType.DELIVERY,
            )
        ).first()
        billing_address = session.exec(
            select(Address).where(
                Address.order_id == order_id,
                Address.type == AddressType.BILLING,
            )
        ).first()
        if not delivery_address:
            raise ValueError(f"Order: [{order_id}] delivery address not found")
        if not billing_address:
            billing_address = delivery_address

        addresses = [delivery_address, billing_address]
        return order, contexts, addresses

    def _build_legacy_payload(
        self,
        session: Session,
        order_id: str,
    ) -> Dict[str, Any]:
        """
        Build payload for the legacy create-order API.

        POST {QPMN_API_URL}/store/orders
        Uses: thirdOrderId, thirdOrderNumber, qty, customizeProject, etc.
        """
        order, contexts, addresses = self._prepare_order_and_skus(session, order_id)
        delivery_address, billing_address = addresses

        line_items: List[Dict[str, Any]] = []
        for idx, ctx in enumerate(contexts, start=1):
            item = ctx["item"]
            sku = ctx["sku"]
            files = ctx["files"]
            properties = ctx["properties"]
            customize_project = ctx["customize_project"]

            # Generate comparisonThumbnail from first file
            first_file_obj = files[0] if files else None
            if first_file_obj:
                first_file_url = first_file_obj.get("url", "")
                thumbnail_url = self._get_order_thumbnail(first_file_url)
                if thumbnail_url:
                    customize_project["comparisonThumbnail"] = thumbnail_url

            line_item = {
                "thirdOrderItemId": sku.sku_id,
                "qty": item.get("quantity", 1),
                "unitPrice": sku.unit_price or 0,
                "storeProductId": sku.sku_id,
                "properties": properties,
                "customizeProject": customize_project,
            }
            # Parallel-card orders carry the generated barcode as the stock
            # number, suffixed with the item's 1-based position (01, 02, ...)
            if order.type == OrderType.PARALLEL_CARD and order.barcode:
                line_item["supplierStockNo"] = f"{order.barcode}{idx:02d}"
            line_items.append(line_item)

        # Order totals mirroring the legacy API's orderTotals block:
        # SUBTOTAL sums unitPrice x qty of the line items above; TAX and
        # SHIPPING have no source in the gateway and default to 0, making
        # ORDER_TOTAL equal to SUBTOTAL.
        subtotal = round(
            sum(
                (li["unitPrice"] or 0) * (li["qty"] or 1)
                for li in line_items
            ),
            2,
        )
        payload = {
            "thirdOrderId": order.order_id,
            "thirdOrderNumber": order.source_order_id,
            "items": line_items,
            "currency": self.fetch_currency_from_qpmn(order.store_id),
            "shippingMethod": self.fetch_shipping_method_from_qpmn(order.store_id),
            "paymentMethod": settings.QPMN_PAYMENT_METHOD,
            "deliveryAddress": self._address_to_legacy_payload(delivery_address),
            "billingAddress": self._address_to_legacy_payload(billing_address),
            "orderTotals": [
                {"name": "TAX", "value": 0.0},
                {"name": "SHIPPING", "value": 0.0},
                {"name": "SUBTOTAL", "value": subtotal},
                {"name": "ORDER_TOTAL", "value": subtotal},
            ],
        }

        logger.info("[OrderService] Built legacy payload for order %s", order_id)
        return payload

    def _build_open_api_payload(
        self,
        session: Session,
        order_id: str,
    ) -> Dict[str, Any]:
        """
        Build payload for the Open API create-order endpoint.

        POST {QPMN_OPEN_API_URL}/orders
        Uses: externalId, externalOrderNumber, quantity, productDesignData, etc.
        """
        order, contexts, addresses = self._prepare_order_and_skus(session, order_id)
        delivery_address, billing_address = addresses

        line_items: List[Dict[str, Any]] = []
        for idx, ctx in enumerate(contexts, start=1):
            item = ctx["item"]
            sku = ctx["sku"]
            properties = ctx["properties"]
            customize_project = ctx["customize_project"]

            product_design_data = self._convert_customize_to_product_design_data(
                customize_project, properties,
            )

            line_item = {
                "externalId": sku.sku_id,
                "unitPrice": sku.unit_price or 0,
                "storeProductId": sku.sku_id,
                "quantity": item.get("quantity", 1),
                "productDesignData": product_design_data,
            }
            # Parallel-card orders carry the generated barcode as the stock
            # number, suffixed with the item's 1-based position (01, 02, ...)
            if order.type == OrderType.PARALLEL_CARD and order.barcode:
                line_item["supplierStockNo"] = f"{order.barcode}{idx:02d}"
            line_items.append(line_item)

        # Fetched once and shared by the top-level currency field and
        # priceInfo.currency (the fetch is an HTTP call to QPMN).
        payload_currency = self.fetch_currency_from_qpmn(order.store_id)
        payload = {
            "externalId": order.order_id,
            "externalOrderNumber": order.source_order_id,
            "shippingMethod": self.fetch_shipping_method_from_qpmn(order.store_id),
            "paymentMethod": settings.QPMN_PAYMENT_METHOD,
            "currency": payload_currency,
            "deliveryAddress": self._address_to_open_api_payload(session, delivery_address),
            "billingAddress": self._address_to_open_api_payload(session, billing_address),
            # Order price summary: subtotal sums the same unitPrice x quantity
            # the line items above carry; discount/shipping/tax have no source
            # in the gateway and default to 0.
            "priceInfo": {
                "currency": payload_currency,
                "subtotal": round(
                    sum(
                        (li["unitPrice"] or 0) * (li["quantity"] or 1)
                        for li in line_items
                    ),
                    2,
                ),
                "discount": 0.0,
                "shipping": 0.0,
                "tax": 0.0,
            },
            "items": line_items,
        }
        logger.info("[OrderService] Built Open API payload for order %s", order_id)
        return payload

    def _convert_customize_to_product_design_data(
        self,
        customize_project: Dict[str, Any],
        properties: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Convert legacy ``customizeProject`` to Open API ``productDesignData``.

        Behavior depends on ``QPMN_ORDER_API_VERSION``:

        - ``open``: ``customizeProject`` already stores the Open API
          structure — pass through ``customizeProject.designData`` directly.
        - otherwise: legacy conversion, ``customizeProject.designs`` →
          ``designData``, ``properties`` → ``designAttributeValues``.
        """
        if settings.QPMN_ORDER_API_VERSION == "open":
            # customizeProject already stores the Open API designData list
            return {"designData": customize_project.get("designData", [])}

        design_attribute_values: List[Dict[str, Any]] = []
        designs = customize_project.get("designs", [])

        # Group designs by materialPath — each unique material becomes one
        # designData entry with multiple views.
        material_groups: Dict[str, List[Dict[str, Any]]] = {}
        material_order: List[str] = []
        for design in designs:
            material_path = design.get("materialPath", "")
            if material_path not in material_groups:
                material_groups[material_path] = []
                material_order.append(material_path)
            material_groups[material_path].append(design)

        design_data: List[Dict[str, Any]] = []
        for material_path in material_order:
            group_designs = material_groups[material_path]
            code = base64.b64encode(material_path.encode()).decode() if material_path else ""

            views: List[Dict[str, Any]] = []
            for gdesign in group_designs:
                side = gdesign.get("side", "")
                page_content_designs = gdesign.get("pageContentDesigns", [])

                view_designs: List[Dict[str, Any]] = []
                for pcd in page_content_designs:
                    effect_images: List[Dict[str, Any]] = []
                    effect = pcd.get("effect")
                    image = pcd.get("image")
                    if effect and image:
                        effect_images.append({"effect": effect, "imageUrl": image})

                    view_designs.append({
                        "index": pcd.get("pageContentIndex", 0),
                        "effectImages": effect_images,
                    })

                views.append({"code": side, "designs": view_designs})

            design_data.append({"code": code, "views": views})

        # Convert properties dict to designAttributeValues list
        for key, value in properties.items():
            design_attribute_values.append({"code": key, "value": value})

        return {
            "designData": design_data,
            "designAttributeValues": design_attribute_values,
        }

    def _address_to_legacy_payload(self, address: Address) -> Dict[str, Any]:
        """Convert Address model to legacy API payload format."""
        return {
            "country": address.country,
            "state": address.state,
            "city": address.city,
            "address_1": address.address1,
            "address_2": address.address2,
            "postcode": address.postcode,
            "first_name": address.first_name,
            "last_name": address.last_name,
            "phone": address.phone,
            "mobile": address.mobile,
            "email": address.email,
            "company": address.company,
        }

    def _address_to_open_api_payload(self, session: Session, address: Address) -> Dict[str, Any]:
        """Convert Address model to Open API payload format."""
        state_code = get_state_code(session, address.state)
        payload: Dict[str, Any] = {
            "countryCode": to_iso_country_code(address.country),
            "country": address.country,
            "state": address.state,
            "city": address.city,
            "address_1": address.address1,
            "address_2": address.address2,
            "postCode": address.postcode,
            "firstName": address.first_name,
            "lastName": address.last_name,
            "phone": address.phone,
            "mobile": address.mobile,
            "email": address.email,
            "company": address.company,
        }
        if state_code:
            payload["stateCode"] = state_code
        return payload

    def _get_order_thumbnail(self, file_url: str) -> Optional[str]:
        """
        Use QPMN existing preview thumbnail URL

        Args:
            file_url: URL of the desing file

        Returns:
            URL of the uploaded thumbnail image, or None if failed
        """
        dimension = 320
        extension = 'png'
        return f"{file_url}/{dimension}/{dimension}/{extension}"

    def fetch_shipping_method_from_qpmn(self, store_id: str) -> str:
        """
        Fetch the default shipping method code from QPMN CGP API.

        Args:
            store_id: Store identifier

        Returns:
            The first shipping method code found, or "Standard" as fallback.
        """
        try:
            store_key = client_service.get_store_key_by_id(store_id)
            if not store_key:
                logger.warning("[OrderService] No store_key found for store_id=%s, falling back to 'Standard'", store_id)
                _capture_shipping_alert("NO_STORE_KEY", store_id=store_id)
                return "Standard"

            api_url = f"{settings.QPMN_API_URL}/store/{store_id}/default/shippingMethod"
            headers = {
                "Authorization": f"Basic {store_key}",
                "Content-Type": "application/json",
            }

            with httpx.Client(timeout=15.0) as client:
                response = client.get(api_url, headers=headers)
                response.raise_for_status()
                data = response.json()

            shippings = (
                data.get("data", {})
                .get("storeDefaultShippings", [])
            )
            if shippings:
                code = shippings[0].get("code", "Standard")
                logger.info("[OrderService] Fetched shipping method '%s' for store_id=%s", code, store_id)
                return code

            logger.warning("[OrderService] No storeDefaultShippings in response for store_id=%s, falling back to 'Standard'", store_id)
            _capture_shipping_alert("EMPTY_RESPONSE", store_id=store_id)
            return "Standard"

        except Exception as e:
            logger.warning("[OrderService] Failed to fetch shipping method for store_id=%s: %s, falling back to 'Standard'", store_id, e)
            _capture_shipping_alert("FETCH_FAILED", store_id=store_id, exc=e)
            return "Standard"

    def fetch_currency_from_qpmn(self, store_id: str) -> str:
        """
        Fetch the store currency code from QPMN CGP API.

        GET {QPMN_API_URL}/partner/stores/{store_id}
        Returns data.currencyCode, or "CNY" as fallback.

        Args:
            store_id: Store identifier

        Returns:
            ISO currency code (e.g. "HKD", "USD"), or "CNY" if unavailable.
        """
        try:
            store_key = client_service.get_store_key_by_id(store_id)
            if not store_key:
                logger.warning("[OrderService] No store_key found for store_id=%s, falling back to 'CNY'", store_id)
                _capture_currency_alert("NO_STORE_KEY", store_id=store_id)
                return "CNY"

            api_url = f"{settings.QPMN_API_URL}/partner/stores/{store_id}"
            headers = {
                "Authorization": f"Basic {store_key}",
                "Content-Type": "application/json",
            }

            with httpx.Client(timeout=15.0) as client:
                response = client.get(api_url, headers=headers)
                response.raise_for_status()
                data = response.json()

            currency_code = data.get("data", {}).get("currencyCode")
            if currency_code:
                logger.info("[OrderService] Fetched currency '%s' for store_id=%s", currency_code, store_id)
                return currency_code

            logger.warning("[OrderService] No currencyCode in response for store_id=%s, falling back to 'CNY'", store_id)
            _capture_currency_alert("EMPTY_RESPONSE", store_id=store_id)
            return "CNY"

        except Exception as e:
            logger.warning("[OrderService] Failed to fetch currency for store_id=%s: %s, falling back to 'CNY'", store_id, e)
            _capture_currency_alert("FETCH_FAILED", store_id=store_id, exc=e)
            return "CNY"
        
# Singleton instance
order_service = OrderService()
