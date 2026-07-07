from fastapi import APIRouter, HTTPException, Query, status, Depends
from sqlmodel import Session, select
from typing import Optional
import uuid
import logging
import httpx
from datetime import datetime,timezone,timezone

logger = logging.getLogger(__name__)

from app.core.database import get_session
from app.models.order import Order, OrderStatus
from app.models.product import Sku, Product
from app.schemas.order import (
    OrderValidationRequest,
    OrderValidationResponse,
    OrderSubmissionRequest,
    OrderSubmissionResponse,
    OrdersListResponse,
    OrderSummary,
    OrderDetailsResponse,
    CancelledOrderResponse,
    FullOrder,
)
from app.core.auth_oneflow import verify_oneflow_auth, get_client_store_id
from app.core.rabbitmq import publish_order_task

router = APIRouter(
    prefix="/api",
    tags=["Orders"],
    dependencies=[Depends(verify_oneflow_auth)],
)


def is_file_accessible(url: str) -> bool:
    """
    Check whether a file URL is reachable without downloading its full content.

    Tries a lightweight HEAD request first and falls back to a streamed GET for
    servers that do not support HEAD. A 2xx/3xx response is treated as accessible.
    """
    try:
        with httpx.Client(timeout=10.0, follow_redirects=True) as client:
            response = client.head(url)
            # Some servers (and presigned OSS URLs) do not allow HEAD.
            if response.status_code in (403, 405, 501):
                with client.stream("GET", url) as stream_response:
                    return stream_response.status_code < 400
            return response.status_code < 400
    except Exception as exc:
        logger.warning(f"[validate_order] File accessibility check failed for {url}: {exc}")
        return False


@router.post("/order/validate", response_model=OrderValidationResponse)
def validate_order(
    request: OrderValidationRequest,
    session: Session = Depends(get_session),
    store_id: Optional[str] = Depends(get_client_store_id),
):
    """
    Validate an Order - Submits an order for validation without actually creating it.

    This endpoint checks if the order data is valid, including:
    - Valid SKU codes
    - Required components present
    - Valid shipping information
    - File accessibility (if fetch=true)

    Returns validation result without persisting the order.
    """
    try:
        # Perform validation logic
        validation_errors = []

        # Validate items
        for item in request.orderData.items:
            # Check if SKU is provided and refers to a valid, active SKU
            if not item.sku:
                validation_errors.append("SKU is required for all items")
            else:
                # Look up the SKU by its business code, scoped to the client's store if set
                sku_query = select(Sku).where(
                    (Sku.sku_id == item.sku) & Sku.active.is_(True)
                )
                if store_id:
                    sku_query = sku_query.where(Sku.store_id == store_id)
                existing_sku = session.exec(sku_query).first()

                if not existing_sku:
                    validation_errors.append(f"Invalid or inactive SKU: {item.sku}")
                else:
                    # Ensure all components marked as required on the product are present
                    product = session.exec(
                        select(Product).where(
                            Product.product_id == existing_sku.product_id
                        )
                    ).first()

                    if product and product.components:
                        provided_codes = {
                            component.code for component in (item.components or [])
                        }
                        for product_component in product.components:
                            if product_component.get("required") and (
                                product_component.get("code") not in provided_codes
                            ):
                                validation_errors.append(
                                    f"Missing required component "
                                    f"'{product_component.get('code')}' for SKU {item.sku}"
                                )

            # Validate components
            if item.components:
                for component in item.components:
                    if component.fetch:
                        if not component.path:
                            validation_errors.append(f"Path required when fetch=true for component {component.code}")
                        elif not is_file_accessible(component.path):
                            validation_errors.append(
                                f"File not accessible for component {component.code}: {component.path}"
                            )

        # Validate shipments
        if request.orderData.shipments:
            for shipment in request.orderData.shipments:
                if shipment.shipTo:
                    if not shipment.shipTo.name and not shipment.shipTo.companyName:
                        validation_errors.append("Either name or companyName required in shipTo")
                    if not shipment.shipTo.isoCountry:
                        validation_errors.append("isoCountry is required in shipTo")

        # If there are validation errors, return failure
        if validation_errors:
            return OrderValidationResponse(
                success=False,
                order={"errors": validation_errors}
            )

        # Validation successful - return validated order structure
        validated_order = {
            "destination": request.destination.model_dump(),
            "orderData": request.orderData.model_dump(),
            "validated_at": datetime.now(timezone.utc).isoformat(),
            "status": "validated"
        }

        return OrderValidationResponse(
            success=True,
            order=validated_order
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Validation failed: {str(e)}"
        )


@router.post("/order", response_model=OrderSubmissionResponse)
def submit_order(
    request: OrderSubmissionRequest,
    session: Session = Depends(get_session),
    store_id: Optional[str] = Depends(get_client_store_id),
):
    """
    Submit an Order - Submits a new print order.

    Creates a new order in the system with the provided data.
    The order will be processed according to the destination configuration.
    The order is associated with the authenticated client's store.
    
    Idempotency: If an order with the same source_order_id already exists for this store,
    returns the existing order instead of creating a duplicate.
    """
    try:
        # Check for existing order with the same source_order_id and store_id (idempotency)
        if store_id:
            existing_order = session.exec(
                select(Order).where(
                    (Order.source_order_id == request.orderData.sourceOrderId) &
                    (Order.store_id == store_id)
                )
            ).first()
            
            if existing_order:
                # Return existing order for idempotency
                full_order = FullOrder(
                    id=existing_order.order_id,
                    v=existing_order.version,
                    destination=existing_order.destination,
                    source=existing_order.source,
                    orderData=existing_order.order_data,
                    logs=existing_order.logs or [],
                    files=existing_order.files or []
                )
                
                return OrderSubmissionResponse(
                    success=True,
                    order=full_order
                )
        
        # Generate unique order ID
        order_id = str(uuid.uuid4())

        # Create order record
        order = Order(
            order_id=order_id,
            source_account=request.destination.name,
            source_order_id=request.orderData.sourceOrderId,
            destination=request.destination.model_dump(),
            source={
                "name": request.destination.name,
                "submitted_at": datetime.now(timezone.utc).isoformat()
            },
            order_data=request.orderData.model_dump(),
            status=OrderStatus.RECEIVED,
            version=0,
            store_id=store_id  # Associate order with client's store
        )

        session.add(order)
        session.commit()
        session.refresh(order)

        # Publish order processing task to RabbitMQ
        task_payload = {
            "order_id": order.order_id,
            "source_order_id": order.source_order_id,
            "status": order.status.value,
            "created_at": order.created_at.isoformat() if order.created_at else None,
        }
        published = publish_order_task(task_payload)
        if not published:
            logger.warning(
                f"Order {order.order_id} created but RabbitMQ publish failed"
            )

        # Build response
        full_order = FullOrder(
            id=order.order_id,
            v=order.version,
            destination=order.destination,
            source=order.source,
            orderData=order.order_data,
            logs=order.logs or [],
            files=order.files or []
        )

        return OrderSubmissionResponse(
            success=True,
            order=full_order
        )

    except Exception as e:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Order submission failed: {str(e)}"
        )


@router.get("/order", response_model=OrdersListResponse)
def get_all_orders(
    page: int = Query(1, ge=1, description="The page number to return"),
    pagesize: int = Query(10, ge=1, le=100, description="Number of orders per page"),
    session: Session = Depends(get_session),
    store_id: Optional[str] = Depends(get_client_store_id),
):
    """
    Get All Orders - Retrieves a paginated list of orders.

    Returns a paginated list of orders for the authenticated client's store.
    If using bootstrap credentials, returns all orders.
    """
    try:
        # Calculate offset
        offset = (page - 1) * pagesize

        # Build query with optional store_id filter
        query = select(Order)
        if store_id:
            query = query.where(Order.store_id == store_id)

        # Get total count
        count_statement = query
        total_count = len(session.exec(count_statement).all())

        # Get paginated orders
        statement = (
            query
            .offset(offset)
            .limit(pagesize)
            .order_by(Order.created_at.desc())
        )
        orders = session.exec(statement).all()

        # Calculate total pages
        total_pages = (total_count + pagesize - 1) // pagesize

        # Build response
        order_summaries = [
            OrderSummary(
                id=order.order_id,
                destination=order.destination,
                source=order.source,
                orderData=order.order_data
            )
            for order in orders
        ]

        return OrdersListResponse(
            success=True,
            count=total_count,
            page=page,
            pages=total_pages,
            data=order_summaries
        )

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve orders: {str(e)}"
        )


@router.get("/order/{order_id}", response_model=OrderDetailsResponse)
def get_order_by_id(
    order_id: str,
    session: Session = Depends(get_session),
    store_id: Optional[str] = Depends(get_client_store_id),
):
    """
    Get an Order - Retrieves detailed information for a specific order by its ID.

    Returns complete order details including all items, components, and status.
    Only returns orders belonging to the authenticated client's store.
    """
    try:
        # Build query with optional store_id filter
        query = select(Order).where(Order.order_id == order_id)
        if store_id:
            query = query.where(Order.store_id == store_id)
        
        order = session.exec(query).first()

        if not order:
            if store_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Order with ID '{order_id}' not found or access denied"
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Order with ID '{order_id}' not found"
                )

        # Build full order response
        full_order = FullOrder(
            id=order.order_id,
            v=order.version,
            destination=order.destination,
            source=order.source,
            orderData=order.order_data,
            logs=order.logs or [],
            files=order.files or []
        )

        return OrderDetailsResponse(
            success=True,
            order=full_order
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve order: {str(e)}"
        )


@router.put("/order/{source_account}/{source_order_id}/cancel", response_model=CancelledOrderResponse)
def cancel_order(
    source_account: str,
    source_order_id: str,
    session: Session = Depends(get_session),
    store_id: Optional[str] = Depends(get_client_store_id),
):
    """
    Cancel Order - Cancels an existing order using the source account and source order ID.

    Attempts to cancel an order that hasn't been completed or shipped.
    Only allows cancellation of orders belonging to the authenticated client's store.
    """
    try:
        # Find order by source account and source order ID
        query = select(Order).where(
            (Order.source_account == source_account) &
            (Order.source_order_id == source_order_id)
        )
        
        # Add store_id filter if using client authentication
        if store_id:
            query = query.where(Order.store_id == store_id)
        
        order = session.exec(query).first()

        if not order:
            if store_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Order with sourceOrderId '{source_order_id}' not found or access denied"
                )
            else:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Order with sourceOrderId '{source_order_id}' not found"
                )

        # Check if order can be cancelled
        if order.status in [OrderStatus.PRINTREADY,OrderStatus.PRINTED, OrderStatus.SHIPPED]:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Cannot cancel order with status '{order.status.value}'"
            )

        # Update order status
        order.status = OrderStatus.CANCELLED
        session.add(order)
        session.commit()

        return CancelledOrderResponse(
            success=True,
            message="Order cancelled successfully",
            order_id=order.order_id
        )

    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Order cancellation failed: {str(e)}"
        )
