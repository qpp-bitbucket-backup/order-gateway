"""Order service for managing order-related operations."""
import logging
import os
import tempfile
import uuid
import fitz  # PyMuPDF
import base64
import httpx
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

from sqlmodel import Session, select
from app.models.product import Sku

from app.models.order import Order, OrderStatus
from app.models.address import Address, AddressType
from app.tasks.orders import publish_order
from app.services.file import file_service
from app.services.client import client_service
from app.core.config import settings

logger = logging.getLogger(__name__)


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

        Returns the existing Order if found, otherwise ``None``.
        """
        query = select(Order).where(Order.source_order_id == source_order_id)
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

        PUT {QPMN_OPEN_API_URL}/store/orders/{store_order_id}/cancel
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
            return {"success": False, "status_code": None, "error": "No store_key configured"}

        api_url = f"{settings.QPMN_OPEN_API_URL}/store/orders/{order.store_order_id}/cancel"
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
            return {"success": False, "status_code": None, "error": "QPMN API timeout"}
        except Exception as exc:
            logger.error("[OrderService] QPMN cancel API request failed for order %s: %s", order.order_id, exc)
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
                qpmn_result = self.update_qpmn_address(order, latest_delivery or new_delivery)
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

    def update_qpmn_address(self, order: Order, addr: Address) -> Dict[str, Any]:
        """
        Call QPMN Open API to update the delivery address for an order.

        PUT {QPMN_OPEN_API_URL}/open-api/v1/orders/{store_order_id}/deliveryAddress
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
        payload = {
            "stateCode": addr.state or "",
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

    def _prepare_order_and_skus(
        self,
        session: Session,
        order_id: str,
    ) -> Tuple[Order, List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Shared helper: load order, iterate items, inject design file URLs.

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
            sku_id = item.get("sku")
            if not sku_id:
                continue

            sku = session.exec(select(Sku).where(Sku.sku_id == sku_id)).first()
            if not sku:
                raise ValueError(f"SKU: [{sku_id}] not found")

            properties = sku.properties or {}
            customize_project = sku.customize_project or {}
            designs = customize_project.get("designs", [])
            files = files_dict.get(f"{sku_id}-{item_index}", [])

            # Inject uploaded file URLs into pageContentDesigns images
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

            contexts.append({
                "item": item,
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
        for ctx in contexts:
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
            line_items.append(line_item)

        payload = {
            "thirdOrderId": order.order_id,
            "thirdOrderNumber": order.source_order_id,
            "items": line_items,
            "currency": self.fetch_currency_from_qpmn(order.store_id),
            "shippingMethod": self.fetch_shipping_method_from_qpmn(order.store_id),
            "paymentMethod": settings.QPMN_PAYMENT_METHOD,
            "deliveryAddress": self._address_to_legacy_payload(delivery_address),
            "billingAddress": self._address_to_legacy_payload(billing_address),
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

        POST {QPMN_OPEN_API_URL}/store/orders
        Uses: externalId, externalOrderNumber, quantity, productDesignData, etc.
        """
        order, contexts, addresses = self._prepare_order_and_skus(session, order_id)
        delivery_address, billing_address = addresses

        line_items: List[Dict[str, Any]] = []
        for ctx in contexts:
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
            line_items.append(line_item)

        payload = {
            "externalId": order.order_id,
            "externalOrderNumber": order.source_order_id,
            "shippingMethod": self.fetch_shipping_method_from_qpmn(order.store_id),
            "paymentMethod": settings.QPMN_PAYMENT_METHOD,
            "currency": self.fetch_currency_from_qpmn(order.store_id),
            "deliveryAddress": self._address_to_open_api_payload(delivery_address),
            "billingAddress": self._address_to_open_api_payload(billing_address),
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

        Legacy ``customizeProject.designs`` → new ``designData``:
          - ``materialPath`` → ``code`` (Base64-encoded)
          - ``side`` → ``views[].code``
          - ``pageContentDesigns[].pageContentIndex`` → ``designs[].index``
          - ``pageContentDesigns[].effect`` → ``effectImages[].effect``
          - ``pageContentDesigns[].image`` → ``effectImages[].imageUrl``

        Legacy ``properties`` → new ``designAttributeValues``.
        """
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
        design_attribute_values: List[Dict[str, Any]] = []
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

    def _address_to_open_api_payload(self, address: Address) -> Dict[str, Any]:
        """Convert Address model to Open API payload format."""
        return {
            "countryCode": address.country,
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
            return "Standard"

        except Exception as e:
            logger.warning("[OrderService] Failed to fetch shipping method for store_id=%s: %s, falling back to 'Standard'", store_id, e)
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
            return "CNY"

        except Exception as e:
            logger.warning("[OrderService] Failed to fetch currency for store_id=%s: %s, falling back to 'CNY'", store_id, e)
            return "CNY"
        
# Singleton instance
order_service = OrderService()
