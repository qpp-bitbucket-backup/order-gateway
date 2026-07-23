from fastapi import APIRouter, HTTPException, Query, status, Depends
from sqlmodel import Session, select
from typing import Optional
from datetime import datetime, timezone
import json
import logging
import httpx

logger = logging.getLogger(__name__)
from app.core.config import settings

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
    OrderUpdateRequest,
    OrderUpdateResponse,
    PlatformOrdersListResponse,
    PlatformOrderSummary,
    PlatformOrderDetailsResponse,
    PlatformFullOrder,
)
from app.core.auth_oneflow import verify_oneflow_auth, get_client_store_id
from app.core.auth_jwt import get_current_user
from app.models.user import User
from app.services.order import order_service

router = APIRouter(
    prefix="/api",
    tags=["Orders"],
    dependencies=[Depends(verify_oneflow_auth)],
)


def _log_request(endpoint: str, request_body: dict):
    """Log request body when DEBUG is enabled."""
    if settings.DEBUG:
        logger.info(
            "[Orders] %s | Request: %s",
            endpoint,
            json.dumps(request_body, ensure_ascii=False, default=str),
        )


def _log_response(endpoint: str, response: dict):
    """Log response body when DEBUG is enabled."""
    if settings.DEBUG:
        logger.info(
            "[Orders] %s | Response: %s",
            endpoint,
            json.dumps(response, ensure_ascii=False, default=str),
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
    _log_request("POST /order/validate", request.model_dump())

    def _error(loc: list, msg: str, error_type: str, input_value=None) -> dict:
        """Build one error object matching FastAPI's standard validation error shape."""
        return {
            "loc": ["body"] + loc,
            "msg": msg,
            "type": error_type,
            "input": input_value,
        }

    # Perform validation logic
    validation_errors: list = []

    # Validate items
    for idx, item in enumerate(request.orderData.items):
        item_loc = ["orderData", "items", idx]

        # Check if SKU is provided and refers to a valid, active SKU
        if not item.sku:
            validation_errors.append(
                _error(item_loc + ["sku"], "SKU is required for all items", "missing")
            )
        else:
            # Look up the SKU by its business code, scoped to the client's store if set
            sku_query = select(Sku).where(
                (Sku.sku_id == item.sku) & Sku.active.is_(True)
            )
            if store_id:
                sku_query = sku_query.where(Sku.store_id == store_id)
            existing_sku = session.exec(sku_query).first()

            if not existing_sku:
                validation_errors.append(
                    _error(item_loc + ["sku"], "Invalid or inactive SKU", "value_error", item.sku)
                )
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
                        required_code = product_component.get("code")
                        if product_component.get("required") and (
                            required_code not in provided_codes
                        ):
                            validation_errors.append(
                                _error(
                                    item_loc + ["components"],
                                    f"Missing required component '{required_code}'",
                                    "missing",
                                    sorted(provided_codes),
                                )
                            )

        # Validate components
        if item.components:
            for cidx, component in enumerate(item.components):
                component_loc = item_loc + ["components", cidx, "path"]
                if component.fetch:
                    if not component.path:
                        validation_errors.append(
                            _error(component_loc, "Path required when fetch=true", "missing")
                        )
                    elif not is_file_accessible(component.path):
                        validation_errors.append(
                            _error(component_loc, "File not accessible", "value_error", component.path)
                        )

    # Validate shipments
    if request.orderData.shipments:
        for sidx, shipment in enumerate(request.orderData.shipments):
            shipment_loc = ["orderData", "shipments", sidx, "shipTo"]
            if shipment.shipTo:
                if not shipment.shipTo.name and not shipment.shipTo.companyName:
                    validation_errors.append(
                        _error(shipment_loc, "Either name or companyName required", "missing")
                    )
                if not shipment.shipTo.isoCountry:
                    validation_errors.append(
                        _error(shipment_loc + ["isoCountry"], "isoCountry is required", "missing")
                    )

    # If there are validation errors, respond with the standard FastAPI validation shape.
    if validation_errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=validation_errors,
        )

    # Validation successful - return validated order structure
    validated_order = {
        "destination": request.destination.model_dump(),
        "orderData": request.orderData.model_dump(),
        "validated_at": datetime.now(timezone.utc).isoformat(),
        "status": "validated"
    }

    resp = OrderValidationResponse(
        success=True,
        order=validated_order
    )
    _log_response("POST /order/validate", resp.model_dump())
    return resp


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

    **Duplicate detection:** If an order with the same `source_order_id` already
    exists for this store, the API returns HTTP **451** with the existing
    `order_id` in the response details.
    """
    _log_request("POST /order", request.model_dump())
    try:
        # Check for duplicate (idempotency)
        existing_order = order_service.check_duplicate(
            session,
            source_order_id=request.orderData.sourceOrderId,
            store_id=store_id,
        )
        if existing_order:
            raise HTTPException(
                status_code=451,
                detail=(
                    f"Order with sourceOrderId '{request.orderData.sourceOrderId}' "
                    f"already exists (order_id={existing_order.order_id})."
                ),
            )

        # Create order via service
        order = order_service.create_order(
            session,
            source_account=request.destination.name,
            source_order_id=request.orderData.sourceOrderId,
            destination=request.destination.model_dump(),
            order_data=request.orderData.model_dump(),
            store_id=store_id,
        )

        # Build response (exclude logs, files, store_order_id)
        full_order = FullOrder(
            id=order.order_id,
            destination=order.destination,
            source=order.source,
            orderData=order.order_data,
            version=order.version,
        )

        resp = OrderSubmissionResponse(success=True, order=full_order)
        _log_response("POST /order", resp.model_dump())
        return resp

    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Order submission failed: {str(e)}",
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
    _log_request("GET /order", {"page": page, "pagesize": pagesize})
    try:
        orders, total_count, total_pages = order_service.get_all_orders(
            session,
            store_id=store_id,
            page=page,
            pagesize=pagesize,
        )

        order_summaries = [
            OrderSummary(
                id=order.order_id,
                destination=order.destination,
                source=order.source,
                orderData=order.order_data,
            )
            for order in orders
        ]

        resp = OrdersListResponse(
            success=True,
            count=total_count,
            page=page,
            pages=total_pages,
            data=order_summaries,
        )
        _log_response("GET /order", resp.model_dump())
        return resp

    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve orders: {str(e)}",
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
    _log_request("GET /order/{order_id}", {"order_id": order_id, "store_id": store_id})
    try:
        order = order_service.get_order_by_id(session, order_id, store_id=store_id)

        if not order:
            detail = (
                f"Order with ID '{order_id}' not found"
                + (" or access denied" if store_id else "")
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)

        full_order = FullOrder(
            id=order.order_id,
            destination=order.destination,
            source=order.source,
            orderData=order.order_data,
            version=order.version,
        )

        resp = OrderDetailsResponse(success=True, order=full_order)
        _log_response("GET /order/{order_id}", resp.model_dump())
        return resp

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve order: {str(e)}",
        )


@router.put(
    "/order/{order_id}",
    response_model=OrderUpdateResponse,
)
def update_order(
    order_id: str,
    request: OrderUpdateRequest,
    session: Session = Depends(get_session),
    store_id: Optional[str] = Depends(get_client_store_id),
):
    """
    Update an Order - Updates the destination and/or order data of an existing order.

    The order can **only** be updated when it is in a cancellable state, i.e. it has
    not yet reached the print-ready stage. Allowed statuses for update:
    `received`, `validated`, `failed`, `errored`.

    Orders with status `printready`, `printed`, `shipped`, or `cancelled` cannot be
    modified and will return HTTP **409 Conflict**.
    """
    _log_request("PUT /order/{order_id}", {"order_id": order_id, **request.model_dump()})
    try:
        order = order_service.get_order_by_id(session, order_id, store_id=store_id)

        if not order:
            detail = (
                f"Order with order_id '{order_id}' not found"
                + (" or access denied" if store_id else "")
            )
            raise HTTPException(status_code=404, detail=detail)

        try:
            order, changes = order_service.update_order(
                session,
                order,
                destination=request.destination.model_dump() if request.destination else None,
                order_data=request.orderData.model_dump() if request.orderData else None,
            )
        except ValueError as ve:
            # Determine 400 vs 409 based on message content
            if "status" in str(ve).lower():
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(ve))
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))

        full_order = FullOrder(
            id=order.order_id,
            destination=order.destination,
            source=order.source,
            orderData=order.order_data,
            version=order.version,
        )

        resp = OrderUpdateResponse(
            success=True,
            message=f"Order updated successfully (fields: {', '.join(changes)}).",
            order=full_order,
        )
        _log_response("PUT /order/{order_id}", resp.model_dump())
        return resp

    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Order update failed: {str(e)}",
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
    _log_request("PUT /order/cancel", {"source_account": source_account, "source_order_id": source_order_id})
    try:
        # Find order by source account and source order ID
        query = select(Order).where(
            (Order.source_account == source_account)
            & (Order.source_order_id == source_order_id)
        )
        print(query)
        if store_id:
            query = query.where(Order.store_id == store_id)
        order = session.exec(query).first()

        if not order:
            detail = (
                f"Order with sourceOrderId '{source_order_id}' not found"
                + (" or access denied" if store_id else "")
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)

        try:
            order = order_service.cancel_order(session, order)
        except ValueError as ve:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(ve))

        resp = CancelledOrderResponse(
            success=True,
            message="Order cancelled successfully",
            order_id=order.order_id,
        )
        _log_response("PUT /order/cancel", resp.model_dump())
        return resp

    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Order cancellation failed: {str(e)}",
        )


# ---------------------------------------------------------------------------
# Platform JWT router – requires JWT Bearer token (for frontend platform)
# ---------------------------------------------------------------------------

jwt_router = APIRouter(
    prefix="/api/platform",
    tags=["Platform"],
)


@jwt_router.get("/orders", response_model=PlatformOrdersListResponse)
def platform_get_orders(
    page: int = Query(1, ge=1, description="The page number to return"),
    pagesize: int = Query(10, ge=1, le=100, description="Number of orders per page"),
    status_filter: Optional[OrderStatus] = Query(None, alias="status", description="Filter by order status"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get Orders (JWT) - Retrieves a paginated list of orders.

    Requires JWT Bearer token. Admin/Editor/Viewer all have access.
    If user has a store_id, only returns orders for that store.
    Returns additional fields: sourceOrderId, logs, files, version, storeId.
    """
    try:
        user_store_id = current_user.store_id

        orders, total_count, total_pages = order_service.get_all_orders(
            session,
            store_id=user_store_id,
            page=page,
            pagesize=pagesize,
            status=status_filter,
        )

        order_summaries = [
            PlatformOrderSummary(
                id=order.order_id,
                sourceOrderId=order.source_order_id,
                destination=order.destination,
                source=order.source,
                orderData=order.order_data,
                status=order.status.value,
                logs=order.logs,
                files=order.files,
                version=order.version,
                storeId=order.store_id,
                storeOrderId=order.store_order_id,
                createdAt=order.created_at.isoformat() if order.created_at else None,
                updatedAt=order.updated_at.isoformat() if order.updated_at else None,
            )
            for order in orders
        ]

        return PlatformOrdersListResponse(
            success=True,
            count=total_count,
            page=page,
            pages=total_pages,
            data=order_summaries,
        )

    except Exception as e:
        logger.error(f"[API] Get order list failed:{str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve orders: {str(e)}",
        )


@jwt_router.get("/orders/{order_id}", response_model=PlatformOrderDetailsResponse)
def platform_get_order(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get Order (JWT) - Retrieves detailed information for a specific order.

    Requires JWT Bearer token. If user has a store_id, only returns orders for that store.
    Returns additional fields: sourceOrderId, logs, files, version, storeId.
    """
    try:
        user_store_id = current_user.store_id
        order = order_service.get_order_by_id(session, order_id, store_id=user_store_id)

        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Order with ID '{order_id}' not found",
            )

        full_order = PlatformFullOrder(
            id=order.order_id,
            sourceOrderId=order.source_order_id,
            destination=order.destination,
            source=order.source,
            orderData=order.order_data,
            status=order.status.value,
            logs=order.logs,
            files=order.files,
            version=order.version,
            storeId=order.store_id,
            storeOrderId=order.store_order_id,
            createdAt=order.created_at.isoformat() if order.created_at else None,
            updatedAt=order.updated_at.isoformat() if order.updated_at else None,
        )

        return PlatformOrderDetailsResponse(success=True, order=full_order)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve order: {str(e)}",
        )
