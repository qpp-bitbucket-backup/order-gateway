from fastapi import APIRouter, HTTPException, Query, status, Depends
from fastapi.responses import JSONResponse
from sqlmodel import Session, select
from typing import Any, Dict, Optional, List
from datetime import datetime, timezone
import json
import logging
import base64
import httpx

logger = logging.getLogger(__name__)
from app.core.config import settings

from app.core.database import get_session
from app.models.order import Order, OrderStatus, OrderType
from app.models.product import Sku, Product
from app.schemas.order import (
    OrderValidationRequest,
    OrderValidationResponse,
    OrderSubmissionRequest,
    OrderSubmissionResponse,
    OrderCreationErrorResponse,
    OrdersListResponse,
    OrderSummary,
    OrderStatusResponse,
    OrderStatusShipment,
    CancelledOrderResponse,
    FullOrder,
    OrderUpdateRequest,
    OrderUpdateResponse,
    PlatformOrdersListResponse,
    PlatformOrderSummary,
    PlatformOrderDetailsResponse,
    PlatformFullOrder,
    MaskedAddress,
    SiteFlowErrorResponse,
    PlatformOrderRepublishResponse,
    PlatformOrderDeleteResponse,
)
from app.core.auth_oneflow import verify_oneflow_auth, get_client_store_id
from app.core.auth_jwt import get_current_user, resolve_scoped_store_id, require_editor_or_above
from app.models.user import User
from app.models.address import Address as AddressModel, AddressType
from app.models.webhook_log import WebhookLog, WebhookDirection
from app.services.order import order_service, OrderNotCancellableError, QPMNCancelError, UPDATABLE_STATUSES, DELETABLE_STATUSES, REPUBLISHABLE_STATUSES, parse_parallel_card_id
from app.services.oss import oss_service
from app.tasks.orders import publish_order

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


# Endpoint label for POST /api/order, reused in DEBUG request/response logs
_POST_ORDER_ENDPOINT = "POST /order"


# Mapping from internal OrderStatus to external status exposed via API
_EXTERNAL_STATUS_MAP: dict = {
    OrderStatus.RECEIVED: "received",
    OrderStatus.PENDING: "received",
    OrderStatus.VALIDATED: "dataready",
    OrderStatus.PROCESSING: "dataready",
    OrderStatus.PRINTREADY: "printready",
    OrderStatus.PRINTED: "printed",
    OrderStatus.PRODUCED: "produced",
    OrderStatus.SHIPPED: "shipped",
    OrderStatus.ERRORED: "error",
    OrderStatus.CANCELLED: "cancelled",
    OrderStatus.FAILED: "error",
}


def _enrich_order_data_with_status(order_data: Optional[dict], status: OrderStatus) -> Optional[dict]:
    """Return a copy of *order_data* with ``status`` injected as external value."""
    if order_data is None:
        return {"status": _EXTERNAL_STATUS_MAP.get(status, status.value)}
    enriched = dict(order_data)
    enriched["status"] = _EXTERNAL_STATUS_MAP.get(status, status.value)
    return enriched


def _with_parallel_card_barcodes(order_data: Optional[dict], barcode: Optional[str]) -> Optional[dict]:
    """Return a copy of *order_data* with item barcodes injected.

    Parallel-card orders submit ``supplierStockNo = barcode + two-digit item
    index`` (e.g. ``"4935768626" + "01"``) to QPMN; the status response
    exposes the same value as ``items[].barcode``. Items are copied so the
    DB-backed ``order_data`` object stays untouched.
    """
    if not barcode or not isinstance(order_data, dict):
        return order_data
    items = order_data.get("items")
    if not isinstance(items, list) or not items:
        return order_data
    enriched = dict(order_data)
    enriched["items"] = [
        {**item, "barcode": f"{barcode}{idx:02d}"} if isinstance(item, dict) else item
        for idx, item in enumerate(items, start=1)
    ]
    return enriched


def _parallel_parent_refs(session: Session, order: Order) -> tuple:
    """Return ``(parent_order_id, parent_source_order_id, parent_store_order_id)``
    for a parallel-card order, or all-``None`` for base-card orders / missing
    parents.
    """
    if order.type != OrderType.PARALLEL_CARD:
        return (None, None, None)
    parent = order_service.find_parallel_card_parent(
        session, order.source_order_id, store_id=order.store_id
    )
    if not parent:
        return (None, None, None)
    return (parent.order_id, parent.source_order_id, parent.store_order_id)


# Internal keys stored in the DB ``source`` JSON column that are not part of
# the SiteFlow ``source`` object and should be stripped from API responses.
_INTERNAL_SOURCE_KEYS = {"submitted_at"}


def _clean_source(source: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Remove internal-only keys from the ``source`` dict."""
    if not source or not isinstance(source, dict):
        return source
    return {k: v for k, v in source.items() if k not in _INTERNAL_SOURCE_KEYS}


def _strip_none(obj: Any) -> Any:
    """Recursively remove ``None`` values from dicts and lists.

    SiteFlow responses never include null-valued fields, so we prune them
    before returning order data to keep responses clean.
    """
    if isinstance(obj, dict):
        return {k: _strip_none(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_strip_none(item) for item in obj]
    return obj


def _mask_pii(value: Optional[str], visible_chars: int = 2) -> Optional[str]:
    """Mask a PII string, keeping only the first *visible_chars* characters."""
    if not value:
        return value
    if len(value) <= visible_chars:
        return value[0] + "***"
    return value[:visible_chars] + "***"


def _mask_email(email: Optional[str]) -> Optional[str]:
    """Mask email: show first 2 chars of local part, mask the rest."""
    if not email:
        return email
    local, _, domain = email.partition("@")
    if not domain:
        return email
    return _mask_pii(local, 2) + "@" + domain


def _mask_postcode(postcode: Optional[str]) -> Optional[str]:
    """Mask postcode: keep first 3 chars."""
    if not postcode:
        return postcode
    if len(postcode) <= 3:
        return postcode[0] + "***"
    return postcode[:3] + "***"


def _build_masked_address(addr: AddressModel) -> MaskedAddress:
    """Build a MaskedAddress from an Address model instance."""
    return MaskedAddress(
        first_name=_mask_pii(addr.first_name, 2),
        last_name=_mask_pii(addr.last_name, 2),
        phone=_mask_pii(addr.phone, 4),
        mobile=_mask_pii(addr.mobile, 4),
        email=_mask_email(addr.email),
        address1=_mask_pii(addr.address1, 4),
        address2=_mask_pii(addr.address2, 4),
        postcode=_mask_postcode(addr.postcode),
        city=addr.city,
        state=addr.state,
        country=addr.country,
        company=_mask_pii(addr.company, 3),
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


def _validation_failed(validations: List[Dict[str, str]]) -> JSONResponse:
    """Build a SiteFlow-compatible 400 "Validation Failed" rejection."""
    error_resp = OrderCreationErrorResponse(
        error={
            "ofError": True,
            "statusCode": 400,
            "code": 208,
            "message": "Validation Failed",
            "validations": validations,
            "mongoErr": True,
        }
    )
    _log_response(_POST_ORDER_ENDPOINT, error_resp.model_dump())
    return JSONResponse(
        status_code=400,
        content=error_resp.model_dump(),
    )


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

    # Check for duplicate source_order_id (CANCELLED/ERRORED orders are
    # excluded — see order_service.check_duplicate).
    existing_order = order_service.check_duplicate(
        session,
        source_order_id=request.orderData.sourceOrderId,
        store_id=store_id,
    )
    if existing_order:
        validation_errors.append(
            _error(
                ["orderData", "sourceOrderId"],
                "Source Order ID already exists",
                "value_error",
                request.orderData.sourceOrderId,
            )
        )

    # Parallel-card detection: a sourceOrderId of the form "aaaaaaa[-_]b_Sccccc"
    # (base-card id + "-"/"_" + version + "_S" + shipping number, e.g.
    # "CN-TEST20260811-1_S1055552" or "IVAN-TEST-0004_1_S10098923"). When the
    # base-card part matches an existing order in the same store,
    # items/shipments validation is skipped entirely.
    parallel_parent = order_service.find_parallel_card_parent(
        session,
        source_order_id=request.orderData.sourceOrderId,
        store_id=store_id,
    )
    if parallel_parent:
        _, version, shipping_no = parse_parallel_card_id(request.orderData.sourceOrderId)
        logger.info(
            "[validate_order] Parallel card order detected: sourceOrderId '%s' "
            "matches base card order %s ('%s', version %s, shipping %s) — skipping items/shipments validation",
            request.orderData.sourceOrderId,
            parallel_parent.order_id,
            parallel_parent.source_order_id,
            version,
            shipping_no,
        )

    if not parallel_parent:
        # Validate items
        for idx, item in enumerate(request.orderData.items):
            item_loc = ["orderData", "items", idx]

            # Check if SKU is provided and refers to a valid, active SKU
            if not item.sku:
                validation_errors.append(
                    _error(item_loc + ["sku"], "SKU is required for all items", "missing")
                )
            else:
                # Look up the SKU by its third-party platform code (source_sku),
                # scoped to the client's store if set
                sku_query = select(Sku).where(
                    (Sku.source_sku == item.sku) & Sku.active.is_(True)
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

    # If there are validation errors, respond in VFS's expected shape:
    # {"success": false, "error": {"message": "...", "name": "...", "code": 422}}
    if validation_errors:
        message = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in validation_errors
        )
        raise _siteflow_error(message, "ValidationError", status.HTTP_422_UNPROCESSABLE_ENTITY)

    # Validation successful - VFS requires orderData at the top level.
    resp = OrderValidationResponse(orderData=request.orderData.model_dump())
    _log_response("POST /order/validate", resp.model_dump())
    return resp


@router.post(
    "/order",
    response_model=OrderSubmissionResponse,
    responses={
        400: {
            "description": "Validation failed — duplicate sourceOrderId detected.",
            "content": {
                "application/json": {
                    "example": {
                        "success": False,
                        "error": {
                            "ofError": True,
                            "statusCode": 400,
                            "code": 208,
                            "message": "Validation Failed",
                            "validations": [
                                {
                                    "path": "orderData.sourceOrderId",
                                    "message": "Source Order ID already exists",
                                }
                            ],
                            "mongoErr": True,
                        }
                    }
                }
            },
        },
        422: {
            "description": "Request body validation failed.",
            "content": {
                "application/json": {
                    "example": {
                        "success": False,
                        "error": {
                            "ofError": True,
                            "statusCode": 422,
                            "message": "Validation Failed",
                            "validations": [
                                {
                                    "path": "orderData.sourceOrderId",
                                    "message": "Field required",
                                }
                            ],
                        }
                    }
                }
            },
        },
        500: {
            "description": "Internal server error during order submission.",
            "content": {
                "application/json": {
                    "example": {
                        "success": False,
                        "error": {
                            "ofError": True,
                            "statusCode": 500,
                            "message": "Order submission failed: internal error",
                        }
                    }
                }
            },
        },
    },
)
def submit_order(
    request: OrderSubmissionRequest,
    session: Session = Depends(get_session),
    store_id: Optional[str] = Depends(get_client_store_id),
):
    """
    Submit an Order - Submits a new print order.

    Creates a new order in the system with the provided data.
    The order payload is uploaded to OSS as a JSON file and a pre-signed
    download URL is included in the response.
    The order is associated with the authenticated client's store.

    **Duplicate detection:** If an order with the same `source_order_id` already
    exists for this store, the API returns HTTP **400** with a SiteFlow-compatible
    error response.

    **File accessibility:** Every item component with `fetch=true` must point
    to a reachable URL (lightweight HEAD/streamed-GET probe, same rule as
    POST /order/validate). Unreachable files yield HTTP **400** with a
    SiteFlow-compatible validation error listing the offending component
    paths. Skipped for parallel card orders.

    **Parallel card orders:** When `sourceOrderId` follows the format
    `aaaaaaa[-_]b_Sccccc` — `aaaaaaa` being the `sourceOrderId` of an existing
    base card order, `b` a version number (separated by `-` or `_`) and
    `ccccc` a shipping number, e.g. `CN-TEST20260811-1_S1055552` or
    `IVAN-TEST-0004_1_S10098923` — the order is treated as a parallel card:
    `items` and `shipments` in the payload are not validated. Parallel card
    payloads still carry items — the schema-level requirement (at least one
    item) is unchanged. The base card lookup is store-scoped, so a parallel
    card must reference a base card order from the same store. If the id
    matches the parallel-card format but no base card order exists, the
    request is rejected with HTTP **400** (SiteFlow-compatible validation
    error).

    **Parallel card version chain:** Submitting version N (N >= 2) requires
    the previous version (same base card, same shipping number, e.g.
    `base-1_Sxxxx` when submitting `base-2_Sxxxx`) to already exist and be
    cancelled. A missing previous version is rejected ("was not submitted"),
    and an active previous version is rejected ("still being processed").
    """
    _log_request(_POST_ORDER_ENDPOINT, request.model_dump())
    try:
        # Check for duplicate (idempotency)
        existing_order = order_service.check_duplicate(
            session,
            source_order_id=request.orderData.sourceOrderId,
            store_id=store_id,
        )
        if existing_order:
            error_resp = OrderCreationErrorResponse(
                error={
                    "ofError": True,
                    "statusCode": 400,
                    "code": 208,
                    "message": "Validation Failed",
                    "validations": [
                        {
                            "path": "orderData.sourceOrderId",
                            "message": "Source Order ID already exists",
                        }
                    ],
                    "mongoErr": True,
                }
            )
            _log_response(_POST_ORDER_ENDPOINT, error_resp.model_dump())
            return JSONResponse(
                status_code=400,
                content=error_resp.model_dump(),
            )

        # Parallel-card detection: a sourceOrderId of the form "aaaaaaa[-_]b_Sccccc"
        # (base-card id + "-"/"_" + version + "_S" + shipping number, e.g.
        # "CN-TEST20260811-1_S1055552" or "IVAN-TEST-0004_1_S10098923"). When the
        # base-card part matches an existing order in the same store,
        # items/shipments validation is skipped entirely — the payload itself
        # still carries items (schema-level requirement). A sourceOrderId that
        # follows the pattern but has no matching base card order is rejected.
        parallel_card_parts = parse_parallel_card_id(request.orderData.sourceOrderId)
        parallel_parent = order_service.find_parallel_card_parent(
            session,
            source_order_id=request.orderData.sourceOrderId,
            store_id=store_id,
        )
        if parallel_parent:
            base_source_order_id, version, shipping_no = parallel_card_parts
            logger.info(
                "[submit_order] Parallel card order detected: sourceOrderId '%s' "
                "matches base card order %s ('%s', version %s, shipping %s) — skipping items/shipments validation",
                request.orderData.sourceOrderId,
                parallel_parent.order_id,
                parallel_parent.source_order_id,
                version,
                shipping_no,
            )
            # Version chain rule: submitting version N (N >= 2) requires the
            # previous version of the same base card AND shipping number to
            # already exist and be cancelled. Otherwise the submission is
            # rejected — a missing predecessor will never be cancelled, and a
            # still-active predecessor is exactly the double-production this
            # check exists to prevent.
            if int(version) >= 2:
                prev_version = int(version) - 1
                # The version separator may be "-" or "_" — accept either form.
                prev_ids = {
                    f"{base_source_order_id}-{prev_version}_S{shipping_no}",
                    f"{base_source_order_id}_{prev_version}_S{shipping_no}",
                }
                prev_query = select(Order).where(
                    Order.source_order_id.in_(prev_ids),
                    Order.is_active.is_(True),
                )
                if store_id:
                    prev_query = prev_query.where(Order.store_id == store_id)
                prev = session.exec(prev_query).first()
                if not prev:
                    logger.warning(
                        "[submit_order] Parallel card '%s' rejected: previous version %s not submitted",
                        request.orderData.sourceOrderId, prev_version,
                    )
                    return _validation_failed([
                        {
                            "path": "orderData.sourceOrderId",
                            "message": (
                                f"Previous version of this parallel card order was not submitted: "
                                f"no active order found for version {prev_version} of base card "
                                f"'{base_source_order_id}' with shipping number '{shipping_no}'"
                            ),
                        }
                    ])
                if prev.status != OrderStatus.CANCELLED:
                    logger.warning(
                        "[submit_order] Parallel card '%s' rejected: previous version '%s' still active (status=%s)",
                        request.orderData.sourceOrderId, prev.source_order_id, prev.status.value,
                    )
                    return _validation_failed([
                        {
                            "path": "orderData.sourceOrderId",
                            "message": (
                                f"Previous version of this parallel card order is still being processed "
                                f"('{prev.source_order_id}', status '{prev.status.value}'); "
                                f"cancel it before submitting version {version}"
                            ),
                        }
                    ])
        elif parallel_card_parts:
            # The id looks like a parallel card but its base card does not exist
            # (in this store) — reject instead of creating an orphan that could
            # never resolve its parent for barcode/address inheritance.
            base_source_order_id, version, shipping_no = parallel_card_parts
            logger.warning(
                "[submit_order] Parallel card id '%s' has no base card order '%s' — rejecting",
                request.orderData.sourceOrderId,
                base_source_order_id,
            )
            return _validation_failed([
                {
                    "path": "orderData.sourceOrderId",
                    "message": (
                        f"Parallel card order references unknown base card order "
                        f"'{base_source_order_id}' (version {version}, shipping {shipping_no})"
                    ),
                }
            ])

        # File accessibility check (same rule as POST /order/validate): every
        # component with fetch=true must point at a reachable URL, otherwise
        # the downstream design-file download would fail. Parallel card
        # orders keep their documented items/shipments validation skip.
        if not parallel_parent:
            file_errors = [
                {
                    "path": f"orderData.items.{idx}.components.{cidx}.path",
                    "message": f"File not accessible: {component.path}",
                }
                for idx, item in enumerate(request.orderData.items)
                for cidx, component in enumerate(item.components or [])
                if component.fetch and component.path and not is_file_accessible(component.path)
            ]
            if file_errors:
                return _validation_failed(file_errors)

        # Create order via service
        order = order_service.create_order(
            session,
            source_account=request.destination.name,
            source_order_id=request.orderData.sourceOrderId,
            destination=request.destination.model_dump(),
            order_data=request.orderData.model_dump(),
            store_id=store_id,
            order_type=OrderType.PARALLEL_CARD if parallel_parent else OrderType.BASE_CARD,
        )

        # Record the parallel-card link on the created order's logs
        if parallel_parent:
            _, version, shipping_no = parse_parallel_card_id(request.orderData.sourceOrderId)
            order.logs = (order.logs or []) + [{
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "parallel_card_detected",
                "message": (
                    f"Parallel card (version {version}, shipping {shipping_no}) of "
                    f"base card order {parallel_parent.order_id} "
                    f"(sourceOrderId '{parallel_parent.source_order_id}'); "
                ),
            }]
            session.add(order)
            session.commit()
            session.refresh(order)

        # Upload order payload to OSS and get pre-signed URL
        oss_url = None
        try:
            oss_object_key = f"orders/{order.order_id}.json"
            oss_url = oss_service.upload_json_and_get_url(oss_object_key, request.model_dump(exclude_none=True))
        except Exception as oss_exc:
            logger.warning("[Orders] OSS upload failed for order %s: %s", order.order_id, oss_exc)

        # Build response (SiteFlow-compatible format)
        source_account_id = base64.b64encode(store_id.encode()).decode() if store_id else None
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"

        resp = OrderSubmissionResponse(
            **{"_id": order.order_id},
            url=oss_url,
            timestamp=timestamp,
            sourceAccountId=source_account_id,
        )
        _log_response(_POST_ORDER_ENDPOINT, resp.model_dump())
        return resp

    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error("[Orders] Order submission failed: %s", e, exc_info=True)
        error_resp = OrderCreationErrorResponse(
            error={
                "ofError": True,
                "statusCode": 500,
                "message": f"Order submission failed: {str(e)}",
            }
        )
        return JSONResponse(
            status_code=500,
            content=error_resp.model_dump(),
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
                orderData=_enrich_order_data_with_status(order.order_data, order.status),
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


def _extract_tracking_info(session: Session, order: Order) -> List[Dict[str, Any]]:
    """Extract the latest tracking data for an order from inbound webhook logs.

    Searches ``webhook_logs`` for the most recent inbound QPMN shipment event
    (``package_shipped`` or ``order_item_*`` with shipments) and returns the
    tracking fields.

    Returns a list of dicts (one per shipment) with keys:
    ``trackingNumber``, ``trackingUrl``, ``company``, ``shipDate``.
    """
    tracking_list: List[Dict[str, Any]] = []
    logs = session.exec(
        select(WebhookLog)
        .where(
            (WebhookLog.order_id == order.order_id)
            & (WebhookLog.direction == WebhookDirection.INBOUND)
            & (WebhookLog.source == "QPMN")
        )
        .order_by(WebhookLog.created_at.desc())
    ).all()

    for log in logs:
        payload = log.payload or {}
        # package_shipped events have tracking fields at the top level
        shipments = payload.get("shipments") or []
        if shipments:
            for s in shipments:
                tracking_list.append({
                    "trackingNumber": s.get("trackingNumber"),
                    "trackingUrl": s.get("trackingUrl"),
                    "company": s.get("company"),
                    "shipDate": s.get("shipDate"),
                })
            break
        # order_item_* events with embedded shipments
        if "trackingNumber" in payload:
            tracking_list.append({
                "trackingNumber": payload.get("trackingNumber"),
                "trackingUrl": payload.get("trackingUrl"),
                "company": payload.get("company"),
                "shipDate": payload.get("shipDate"),
            })
            break

    return tracking_list


def _build_order_status_shipments(
    order: Order,
    tracking_list: List[Dict[str, Any]],
) -> Optional[List[OrderStatusShipment]]:
    """Build the top-level ``shipments`` array for the order status response.

    Merges carrier info from ``order.order_data["shipments"]`` with tracking
    data extracted from webhook logs.  When the order has no shipments
    defined in ``order_data`` but tracking data exists from webhook events,
    those tracking entries are used directly.
    """
    order_data = order.order_data or {}
    order_shipments = order_data.get("shipments") or []

    if not order_shipments and not tracking_list:
        return None

    def _parse_shipped_date(raw: Any) -> Optional[str]:
        """Convert epoch-ms or ISO string to ``YYYY-MM-DDTHH:MM:SS.mmmZ``."""
        if not raw:
            return None
        try:
            ts = int(raw)
            return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
        except (ValueError, TypeError):
            # Already an ISO-8601 string — return as-is
            return str(raw)

    def _carrier_from_tracking(tracking: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Build a minimal carrier dict from webhook ``company`` field."""
        company = tracking.get("company")
        if company:
            return {"code": company}
        return None

    result: List[OrderStatusShipment] = []

    if order_shipments:
        for idx, ship in enumerate(order_shipments):
            carrier = ship.get("carrier") if isinstance(ship, dict) else None
            tracking = tracking_list[idx] if idx < len(tracking_list) else {}

            # Webhook company overrides order_data carrier when present
            if not carrier:
                carrier = _carrier_from_tracking(tracking)

            result.append(OrderStatusShipment(
                carrier=carrier,
                shippedDate=_parse_shipped_date(tracking.get("shipDate")),
                trackingNumber=tracking.get("trackingNumber"),
                trackingUrl=tracking.get("trackingUrl"),
                status=order.status.value if idx == 0 else None,
                shipmentIndex=ship.get("shipmentIndex", idx) if isinstance(ship, dict) else idx,
            ))
    else:
        # No shipments in order_data — use tracking entries directly
        for idx, tracking in enumerate(tracking_list):
            result.append(OrderStatusShipment(
                carrier=_carrier_from_tracking(tracking),
                shippedDate=_parse_shipped_date(tracking.get("shipDate")),
                trackingNumber=tracking.get("trackingNumber"),
                trackingUrl=tracking.get("trackingUrl"),
                status=order.status.value if idx == 0 else None,
                shipmentIndex=idx,
            ))

    return result if result else None


@router.get(
    "/order/details/{order_id}",
    response_model=OrderStatusResponse,
    responses={
        404: {
            "description": "Order not found.",
            "content": {"application/json": {"example": {
                "success": False,
                "error": {
                    "ofError": True,
                    "statusCode": 404,
                    "code": 211,
                    "message": "Order not found",
                },
            }}},
        },
        500: {
            "description": "Internal server error.",
            "content": {"application/json": {"example": {
                "success": False,
                "error": {
                    "ofError": True,
                    "statusCode": 500,
                    "message": "Failed to retrieve order",
                },
            }}},
        },
    },
)
def get_order_status(
    order_id: str,
    includes: Optional[List[str]] = Query(None, alias="includes[]", description="Additional data to include (e.g., shipments)"),
    session: Session = Depends(get_session),
    store_id: Optional[str] = Depends(get_client_store_id),
):
    """
    Get Order Status - Retrieves order status in SiteFlow-compatible format.

    Returns order details with top-level ``shipments`` array containing carrier
    and tracking information.

    **Error responses** follow the SiteFlow format:
    ``{"success": false, "error": {"ofError": true, "statusCode": 404, "code": 211, "message": "Order not found"}}``
    """
    _log_request("GET /order/details/{order_id}", {"order_id": order_id, "includes": includes})
    try:
        order = order_service.get_order_by_id(session, order_id, store_id=store_id)

        if not order:
            error_resp = OrderCreationErrorResponse(
                error={
                    "ofError": True,
                    "statusCode": 404,
                    "code": 211,
                    "message": "Order not found",
                }
            )
            return JSONResponse(status_code=404, content=error_resp.model_dump())

        # Build order object with enriched status
        order_data = _enrich_order_data_with_status(order.order_data, order.status)
        # Parallel-card orders expose each item's QPMN supplierStockNo
        # (order barcode + two-digit item index) as orderData.items[].barcode.
        if order.type == OrderType.PARALLEL_CARD:
            order_data = _with_parallel_card_barcodes(order_data, order.barcode)
        full_order = FullOrder(
            id=order.order_id,
            destination=order.destination,
            source=_clean_source(order.source),
            orderData=order_data,
            version=order.version,
        )

        # Build shipments array when includes[]=shipments is requested
        shipments: List[OrderStatusShipment] = []
        if includes and "shipments" in includes:
            tracking_list = _extract_tracking_info(session, order)
            shipments = _build_order_status_shipments(order, tracking_list) or []

        resp = OrderStatusResponse(
            order=_strip_none(full_order.model_dump(by_alias=True)),
            orderId=order.order_id,
            shipments=shipments,
        )
        _log_response("GET /order/details/{order_id}", resp.model_dump())
        return resp

    except Exception as e:
        logger.error("[Orders] Failed to get order status: %s", e, exc_info=True)
        error_resp = OrderCreationErrorResponse(
            error={
                "ofError": True,
                "statusCode": 500,
                "message": f"Failed to retrieve order: {str(e)}",
            }
        )
        return JSONResponse(status_code=500, content=error_resp.model_dump())


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
    Update an Order - Updates an existing order's address or content.

    **Address update** (when ``orderData.shipments`` has items):
    Checks that the order status allows updates, then fetches the latest
    delivery address from OMS and pushes it to QPMN via the update-address
    API.

    **Content update** (when ``orderData.shipments`` is empty or absent):
    Treats this as a content/artwork change. The current order is cancelled
    first via QPMN; if the order cannot be cancelled (e.g. ``printready`` /
    ``printed`` / ``shipped`` / ``cancelled``), the update is rejected with
    HTTP **409**. On success, a new order is created with the same
    ``sourceOrderId`` but a different platform order id — the response's
    ``order`` is the new order, and ``cancelledOrderId`` is the old one's id.
    Requires both ``destination`` and ``orderData`` in the request body.
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

        # --- Determine request type ---
        has_shipments = (
            request.orderData is not None
            and request.orderData.shipments
            and len(request.orderData.shipments) > 0
        )

        if has_shipments:
            # === Address update path ===
            # 1. Check order status is updatable
            if order.status not in UPDATABLE_STATUSES:
                order.logs = (order.logs or []) + [{
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "action": "address_update_rejected",
                    "message": f"Cannot update order with status '{order.status.value}'.",
                }]
                session.add(order)
                session.commit()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Cannot update order with status '{order.status.value}'."
                    ),
                )

            # 2. Update order_data in DB (persist new shipments, but don't bump version yet)
            if request.orderData is not None:
                order.order_data = request.orderData.model_dump()
            if request.destination is not None:
                order.destination = request.destination.model_dump()
            session.add(order)
            session.commit()
            session.refresh(order)

            # 3. Fetch address from OMS and update QPMN
            try:
                address_result = order_service.sync_order_address(session, order)
            except Exception as addr_exc:
                logger.warning("[Orders] Address sync failed for order %s: %s", order.order_id, addr_exc)
                address_result = {"address_changed": False, "qpmn_updated": None, "error": str(addr_exc)}

            full_order = FullOrder(
                id=order.order_id,
                destination=order.destination,
                source=order.source,
                orderData=order.order_data,
                version=order.version,
            )

            # Build response message and record the outcome in order logs
            if address_result.get("address_changed"):
                qpmn_updated = address_result.get("qpmn_updated")
                if qpmn_updated:
                    msg = "Address updated in QPMN successfully."
                    action = "address_update_succeeded"
                    # Bump version only on success
                    order.version += 1
                elif qpmn_updated is False:
                    # Failure log already written by sync_order_address
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                        detail="Address changed but QPMN update failed.",
                    )
                else:
                    msg = "Address changed but no store_order_id (skipped QPMN)."
                    action = "address_update_skipped"
                    # Bump version (address changed locally even if QPMN was skipped)
                    order.version += 1
            elif address_result.get("error"):
                msg = f"Address sync failed: {address_result['error']}"
                action = "address_update_failed"
            else:
                msg = "Address unchanged."
                action = "address_update_unchanged"

            order.logs = (order.logs or []) + [{
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": action,
                "message": msg,
            }]
            session.add(order)
            session.commit()
            session.refresh(order)
            full_order.version = order.version

            resp = OrderUpdateResponse(
                success=True,
                message=msg,
                order=full_order,
            )
            _log_response("PUT /order/{order_id}", resp.model_dump())
            return resp

        else:
            # === Content update path ===
            # Recreating the order requires full destination/orderData — can't
            # cancel the original on a partial payload we then can't rebuild from.
            if request.destination is None or request.orderData is None:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Content update requires both 'destination' and 'orderData' to recreate the order.",
                )

            old_order_id = order.order_id
            old_source_order_id = order.source_order_id

            # Cancel current order first; if it can't be cancelled, it can't be updated.
            try:
                order = order_service.cancel_order(session, order)
            except OrderNotCancellableError:
                order.logs = (order.logs or []) + [{
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "action": "content_update_rejected",
                    "message": (
                        f"Cannot update order content: order status "
                        f"'{order.status.value}' does not allow cancellation."
                    ),
                }]
                session.add(order)
                session.commit()
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        f"Cannot update order content: order status "
                        f"'{order.status.value}' does not allow cancellation, "
                        f"which is required for content updates."
                    ),
                )
            except QPMNCancelError as qpmn_err:
                raise HTTPException(
                    status_code=status.HTTP_502_BAD_GATEWAY,
                    detail=f"Cannot cancel order for content update: {str(qpmn_err)}",
                )

            # Re-create the order with the new content, same source_order_id,
            # different platform order id — mirrors the previous standalone
            # artwork-update endpoint's contract.
            order_data = request.orderData.model_dump()
            order_data["sourceOrderId"] = old_source_order_id

            new_order = order_service.create_order(
                session,
                source_account=request.destination.name,
                source_order_id=old_source_order_id,
                destination=request.destination.model_dump(),
                order_data=order_data,
                store_id=store_id,
                # Content updates recreate the same order — keep its type
                order_type=order.type,
            )

            full_order = FullOrder(
                id=new_order.order_id,
                destination=new_order.destination,
                source=new_order.source,
                orderData=new_order.order_data,
                version=new_order.version,
            )

            resp = OrderUpdateResponse(
                success=True,
                message="Order content updated: original order cancelled and a new order created.",
                order=full_order,
                cancelledOrderId=old_order_id,
            )
            _log_response("PUT /order/{order_id}", resp.model_dump())
            return resp

    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error("[Orders] Order update failed: %s", str(e))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Order update failed: {str(e)}",
        )


def _siteflow_error(message: str, name: str, code: int) -> HTTPException:
    """Build a SiteFlow-compatible HTTPException.

    SiteFlow error response format:
        {"success": false, "error": {"message": "...", "name": "...", "code": 404}}
    """
    error_resp = SiteFlowErrorResponse(
        success=False,
        error={"message": message, "name": name, "code": code},
    )
    return HTTPException(status_code=code, detail=error_resp.model_dump())


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

    Error responses follow the HP SiteFlow API format:
    ``{"success": false, "error": {"message": "...", "name": "...", "code": 404}}``

    | HTTP Code | Error Name        | Scenario                                      |
    |-----------|-------------------|-----------------------------------------------|
    | 404       | NotFound          | Order not found or access denied              |
    | 409       | Conflict          | Order status does not allow cancellation      |
    | 502       | BadGateway        | QPMN cancel API call failed                   |
    | 500       | InternalServerError| Unexpected internal error                    |
    """
    _log_request("PUT /order/cancel", {"source_account": source_account, "source_order_id": source_order_id})
    try:
        # Find order by source account and source order ID
        query = select(Order).where(
            (Order.source_account == source_account)
            & (Order.source_order_id == source_order_id)
        )
        if store_id:
            query = query.where(Order.store_id == store_id)
        order = session.exec(query).first()

        if not order:
            detail = (
                f"Order with sourceOrderId '{source_order_id}' not found"
                + (" or access denied" if store_id else "")
            )
            raise _siteflow_error(detail, "NotFound", 404)

        try:
            order = order_service.cancel_order(session, order)
        except OrderNotCancellableError:
            raise _siteflow_error(
                f"Cannot cancel order with status '{order.status.value}'.",
                "Conflict",
                409,
            )
        except QPMNCancelError as qpmn_err:
            raise _siteflow_error(
                str(qpmn_err),
                "BadGateway",
                502,
            )

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
        raise _siteflow_error(
            f"Order cancellation failed: {str(e)}",
            "InternalServerError",
            500,
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
    status_filter: Optional[List[OrderStatus]] = Query(None, alias="status[]", description="Filter by order status (supports multiple values, e.g. status[]=failed&status[]=errored)"),
    store_id: Optional[str] = Query(None, description="Filter by store ID"),
    source_order_id: Optional[str] = Query(None, alias="sourceOrderId", description="Fuzzy search by source order ID"),
    session: Session = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Get Orders (JWT) - Retrieves a paginated list of orders.

    Requires JWT Bearer token. Admin/Editor/Viewer all have access.
    If user has a store_id, only returns orders for that store.
    Admin users can optionally filter by store_id query parameter.
    Supports filtering by multiple statuses: ?status[]=failed&status[]=errored
    Supports fuzzy search on sourceOrderId: ?sourceOrderId=ORD-12
    Returns additional fields: sourceOrderId, logs, files, version, storeId.
    """
    # Outside the try block so the 403 for out-of-scope stores is not
    # swallowed into a 500 by the generic exception handler below.
    # ADMIN is unrestricted (optional store_id filter honored as-is);
    # EDITOR/VIEWER are always scoped to their own store and cannot
    # fall back to seeing other stores' data.
    effective_store_id = resolve_scoped_store_id(current_user, store_id)
    try:
        orders, total_count, total_pages = order_service.get_all_orders(
            session,
            store_id=effective_store_id,
            page=page,
            pagesize=pagesize,
            statuses=status_filter,
            source_order_id=source_order_id,
        )

        order_summaries = []
        for order in orders:
            parent_order_id, parent_source_order_id, parent_store_order_id = _parallel_parent_refs(session, order)
            order_summaries.append(PlatformOrderSummary(
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
                type=order.type.value,
                creationPayload=order.creation_payload,
                parentSourceOrderId=parent_source_order_id,
                parentOrderId=parent_order_id,
                parentStoreOrderId=parent_store_order_id,
                createdAt=order.created_at.isoformat() if order.created_at else None,
                updatedAt=order.updated_at.isoformat() if order.updated_at else None,
            ))

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

    Requires JWT Bearer token. EDITOR/VIEWER users only see orders for
    their own store; ADMIN is unrestricted.
    Returns additional fields: sourceOrderId, logs, files, version, storeId.
    """
    try:
        scoped_store_id = resolve_scoped_store_id(current_user)
        order = order_service.get_order_by_id(session, order_id, store_id=scoped_store_id)

        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Order with ID '{order_id}' not found",
            )

        # Fetch the latest delivery and billing address records for this order
        delivery_address = session.exec(
            select(AddressModel)
            .where(
                (AddressModel.order_id == order.order_id)
                & (AddressModel.type == AddressType.DELIVERY)
            )
            .order_by(AddressModel.created_at.desc())  # type: ignore[union-attr]
        ).first()
        billing_address = session.exec(
            select(AddressModel)
            .where(
                (AddressModel.order_id == order.order_id)
                & (AddressModel.type == AddressType.BILLING)
            )
            .order_by(AddressModel.created_at.desc())  # type: ignore[union-attr]
        ).first()
        masked_delivery = _build_masked_address(delivery_address) if delivery_address else None
        masked_billing = _build_masked_address(billing_address) if billing_address else None

        # Fetch webhook logs for this order, sorted by created_at ascending
        webhook_logs = session.exec(
            select(WebhookLog)
            .where(WebhookLog.order_id == order.order_id)
            .order_by(WebhookLog.created_at.asc())  # type: ignore[union-attr]
        ).all()
        webhooks = [
            {
                "id": log.id,
                "direction": log.direction.value,
                "source": log.source,
                "eventStatus": log.event_status,
                "processStatus": log.process_status.value,
                "eventId": log.event_id,
                "storeOrderId": log.store_order_id,
                "storeOrderItemId": log.store_order_item_id,
                "payload": log.payload,
                "details": log.details,
                "retryCount": log.retry_count,
                "createdAt": log.created_at.isoformat() if log.created_at else None,
            }
            for log in webhook_logs
        ] if webhook_logs else None

        parent_order_id, parent_source_order_id, parent_store_order_id = _parallel_parent_refs(session, order)
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
            type=order.type.value,
            creationPayload=order.creation_payload,
            parentSourceOrderId=parent_source_order_id,
            parentOrderId=parent_order_id,
            parentStoreOrderId=parent_store_order_id,
            createdAt=order.created_at.isoformat() if order.created_at else None,
            updatedAt=order.updated_at.isoformat() if order.updated_at else None,
            deliveryAddress=masked_delivery,
            billingAddress=masked_billing,
            webhooks=webhooks,
        )

        return PlatformOrderDetailsResponse(success=True, order=full_order)

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve order: {str(e)}",
        )


@jwt_router.post("/orders/{order_id}/republish", response_model=PlatformOrderRepublishResponse)
def platform_republish_order(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_editor_or_above),
):
    """
    Republish Order (JWT) - Reset an order and re-enqueue it for publishing.

    Requires JWT Bearer token with editor or admin role. EDITOR users can
    only republish orders belonging to their own store; ADMIN is unrestricted.

    An order may only be republished when:
    - its status is ``received`` or ``failed``, and
    - it has no ``storeOrderId`` yet (never successfully accepted by QPMN), and
    - no other order in the same store shares its ``sourceOrderId`` (would
      otherwise create a duplicate push on the QPMN side).

    When both conditions hold, the order status is reset to ``received`` and
    a new publish task is enqueued to the ``order_publishing`` queue, which
    re-runs the whole pipeline (received -> pending -> validated -> ...).
    """
    # Outside the try block so the 403 for out-of-scope stores is not
    # swallowed into a 500 by the generic exception handler below.
    scoped_store_id = resolve_scoped_store_id(current_user)
    try:
        order = order_service.get_order_by_id(session, order_id, store_id=scoped_store_id)
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Order with ID '{order_id}' not found",
            )

        # Condition 1: only received/failed orders may be republished —
        # anything else is already in flight or needs a different remedy.
        if order.status not in REPUBLISHABLE_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Order with ID '{order_id}' has status '{order.status.value}' and "
                    "cannot be republished; only received or failed orders can be republished"
                ),
            )

        # Condition 2: only orders that were never accepted by QPMN (no
        # store_order_id assigned yet) may be republished.
        if order.store_order_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Order with ID '{order_id}' has already been accepted by QPMN "
                    f"(storeOrderId='{order.store_order_id}') and cannot be republished"
                ),
            )

        # Condition 3: no other order in the same store may share this
        # source_order_id. SQL ``NULL = NULL`` never matches, so orders
        # without a store are compared through ``IS NULL`` instead.
        # Soft-deleted orders (is_active=false) no longer occupy their
        # source_order_id (mirrors check_duplicate) and are ignored here.
        duplicate_query = select(Order).where(
            Order.source_order_id == order.source_order_id,
            Order.order_id != order.order_id,
            Order.is_active.is_(True),
        )
        if order.store_id:
            duplicate_query = duplicate_query.where(Order.store_id == order.store_id)
        else:
            duplicate_query = duplicate_query.where(Order.store_id.is_(None))  # type: ignore[union-attr]
        duplicate = session.exec(duplicate_query).first()
        if duplicate:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Another order with sourceOrderId '{order.source_order_id}' already "
                    f"exists in the same store (order_id='{duplicate.order_id}'); "
                    "republish is not allowed"
                ),
            )

        # Reset the status back to received; publish_order() validates the
        # received -> pending transition itself before re-running.
        order.status = OrderStatus.RECEIVED
        order.logs = (order.logs or []) + [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "order_republish_requested",
                "message": (
                    f"Republish requested via platform API by user "
                    f"'{current_user.username}'; status reset to received"
                ),
                "worker": "platform_api",
            },
        ]
        session.add(order)
        session.commit()
        session.refresh(order)

        # Enqueue the publish task with the same payload shape as create_order()
        task_payload = {
            "order_id": order.order_id,
            "source_order_id": order.source_order_id,
            "status": order.status.value,
            "created_at": order.created_at.isoformat() if order.created_at else None,
        }
        publish_order.apply_async(args=[task_payload])

        return PlatformOrderRepublishResponse(
            success=True,
            message=f"Order '{order_id}' reset to received and queued for publishing",
            orderId=order.order_id,
            status=order.status.value,
        )

    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"[API] Republish order failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to republish order: {str(e)}",
        )


@jwt_router.delete("/orders/{order_id}", response_model=PlatformOrderDeleteResponse)
def platform_delete_order(
    order_id: str,
    session: Session = Depends(get_session),
    current_user: User = Depends(require_editor_or_above),
):
    """
    Delete Order (JWT) - Soft-delete an order that was never accepted by QPMN.

    Requires JWT Bearer token with editor or admin role. EDITOR users can
    only delete orders belonging to their own store; ADMIN is unrestricted.

    An order may only be deleted when:
    - it has no ``storeOrderId`` yet (never successfully accepted by QPMN), and
    - its status is ``failed`` or ``cancelled``.

    Deletion is soft: ``is_active`` is set to ``false``, which hides the order
    from queries and frees its ``sourceOrderId`` for re-submission, while the
    row itself is kept for auditing together with an ``order_deleted`` log
    entry recording who deleted it.
    """
    # Outside the try block so the 403 for out-of-scope stores is not
    # swallowed into a 500 by the generic exception handler below.
    scoped_store_id = resolve_scoped_store_id(current_user)
    try:
        order = order_service.get_order_by_id(session, order_id, store_id=scoped_store_id)
        if not order:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Order with ID '{order_id}' not found",
            )

        # Condition 1: only orders that were never accepted by QPMN (no
        # store_order_id assigned yet) may be deleted.
        if order.store_order_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Order with ID '{order_id}' has already been accepted by QPMN "
                    f"(storeOrderId='{order.store_order_id}') and cannot be deleted"
                ),
            )

        # Condition 2: only terminal failed/cancelled orders may be deleted;
        # anything else is either still in flight or already beyond recall.
        if order.status not in DELETABLE_STATUSES:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Order with ID '{order_id}' has status '{order.status.value}' and "
                    "cannot be deleted; only failed or cancelled orders without a "
                    "storeOrderId can be deleted"
                ),
            )

        # Soft delete: keep the row (auditing) but hide it from queries and
        # release its source_order_id for re-submission.
        order.is_active = False
        order.logs = (order.logs or []) + [
            {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "order_deleted",
                "message": (
                    f"Soft-deleted via platform API by user "
                    f"'{current_user.username}'; is_active set to false"
                ),
                "worker": "platform_api",
            },
        ]
        session.add(order)
        session.commit()

        return PlatformOrderDeleteResponse(
            success=True,
            message=f"Order '{order_id}' deleted",
            orderId=order.order_id,
        )

    except HTTPException:
        raise
    except Exception as e:
        session.rollback()
        logger.error(f"[API] Delete order failed: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to delete order: {str(e)}",
        )
