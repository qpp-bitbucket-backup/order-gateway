"""Order service for managing order-related operations."""
import logging
import os
import tempfile
import uuid
import fitz  # PyMuPDF
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any, Tuple

from sqlmodel import Session, select
from app.models.product import Sku

from app.models.order import Order, OrderStatus
from app.models.address import Address, AddressType
from app.tasks.orders import publish_order
from app.services.file import file_service
from app.services.client import client_service

logger = logging.getLogger(__name__)


# Statuses that allow updates (order has not reached print-ready stage)
UPDATABLE_STATUSES = {
    OrderStatus.RECEIVED,
    OrderStatus.PENDING,
    OrderStatus.VALIDATED,
    OrderStatus.FAILED,
    OrderStatus.ERRORED,
}

# Statuses that do NOT allow cancellation
NON_CANCELLABLE_STATUSES = {
    OrderStatus.PRINTREADY,
    OrderStatus.PRINTED,
    OrderStatus.SHIPPED,
}


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
        status: Optional[OrderStatus] = None,
    ) -> Tuple[List[Order], int, int]:
        """
        Retrieve a paginated list of orders.

        Returns:
            A tuple of (orders, total_count, total_pages).
        """
        query = select(Order)
        if store_id:
            query = query.where(Order.store_id == store_id)
        if status:
            query = query.where(Order.status == status)

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
                f"Cannot update order with status '{order.status.value}'. "
                f"Order must be in one of: {', '.join(s.value for s in UPDATABLE_STATUSES)}."
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

    def cancel_order(
        self,
        session: Session,
        order: Order,
    ) -> Order:
        """
        Cancel an existing order.

        Raises:
            ValueError: If the order status does not allow cancellation.
        """
        if order.status in NON_CANCELLABLE_STATUSES:
            raise ValueError(
                f"Cannot cancel order with status '{order.status.value}'."
            )

        order.status = OrderStatus.CANCELLED
        session.add(order)
        session.commit()

        logger.info("[OrderService] Cancelled order %s", order.order_id)
        return order

    def merge_order(
        self,
        session: Session,
        order_id: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Merge order customization data into the order's items customizeProject.
        Build the payload for pushing order to QPMN platform.
        Returns the payload dict, or None if order/SKU not found.
        """

        order = session.exec(select(Order).where(Order.order_id == order_id)).first()
        if not order:
            raise ValueError(f"Order: [{order_id}] not found")

        files_dict = order.files
        order_data = order.order_data or {}
        line_items = []

        for item_index, item in enumerate(order_data.get("items",[])):
            sku_id = item.get("sku")
            if not sku_id:
                continue

            sku = session.exec(select(Sku).where(Sku.sku_id == sku_id)).first()
            if not sku:
                raise ValueError(f"SKU: [{sku_id}] not found")

            properties = sku.properties or {}
            customize_properties = sku.customize_project or {}
            designs = customize_properties.get("designs", [])
            files = files_dict.get(f"{sku_id}-{item_index}", []);
            for index, design in enumerate(designs):
                file_obj = files[index] if index < len(files) else None
                if not file_obj:
                    raise ValueError(f"SKU: [{sku_id}] design file not found")
                file_url = file_obj.get("url", None)
                if not file_url:
                    raise ValueError(f"SKU: [{sku_id}] design file not found")
                # Replace pageContentDesigns image with file url
                page_content_designs = design.get("pageContentDesigns", [])
                for pcd in page_content_designs:
                    if "image" in pcd:
                        pcd["image"] = file_url
                design["pageContentDesigns"] = page_content_designs

            # Generate comparisonThumbnail from first file if it's a PDF
            first_file_obj = files[0] if files else None
            if first_file_obj:
                first_file_url = first_file_obj.get("url", "")
                thumbnail_url = self._get_order_thumbnail(first_file_url)
                if thumbnail_url:
                    customize_properties["comparisonThumbnail"] = thumbnail_url

            line_item = {
                "thirdOrderItemId": sku_id,
                "qty": item.get("quantity", 1),
                "unitPrice": sku.unit_price or 0,
                "storeProductId": sku.sku_id,
                "properties": properties,
                "customizeProject": customize_properties,
            }
            line_items.append(line_item)
        payload = {
            "thirdOrderId": order.order_id,
            "thirdOrderNumber": order.source_order_id,
            "items": line_items,
            "currency": "CNY"
        }

        # Query addresses for this order
        delivery_address = session.exec(
            select(Address).where(
                Address.order_id == order_id,
                Address.type == AddressType.DELIVERY
            )
        ).first()
        billing_address = session.exec(
            select(Address).where(
                Address.order_id == order_id,
                Address.type == AddressType.BILLING
            )
        ).first()
        if not delivery_address:
            raise ValueError(f"Order: [{order_id}] delivery address not found")
        
        payload["deliveryAddress"] = self._address_to_payload(delivery_address)
        
        if not billing_address:
            billing_address = delivery_address
        
        payload["billingAddress"] = self._address_to_payload(billing_address)

        logger.info("[OrderService] Built push payload for order %s", order_id)
        return payload

    def _address_to_payload(self, address: Address) -> Dict[str, Any]:
        """Convert Address model to payload format."""
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
        
# Singleton instance
order_service = OrderService()
