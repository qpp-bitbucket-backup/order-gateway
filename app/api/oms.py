"""OMS cancel / return APIs.

Two endpoints for the OMS system, authenticated with the same signed-header
mechanism VFS uses (app/core/auth_oms.py — per-client oms_token/oms_secret
from the clients table):

- POST /api/oms/order/{source_order_id}/cancel — cancel by sourceOrderId
  (no source_account needed, unlike the SiteFlow cancel endpoint).
- POST /api/oms/order/{source_order_id}/return  — initiate a return on a
  SHIPPED order (QPMN return API; only shipped orders are returnable).

``source_order_id`` is not globally unique (cross-store collisions, and the
artwork-update flow reuses an id after cancelling), so both endpoints look
up ALL active orders for the id: exactly one → proceed, more than one →
409 with the candidate list. With none, the cancel endpoint accepts the
request as a deferred cancellation (200, recorded in order_cancel_list
and applied when the order arrives), while the return endpoint 404s
(returns need a shipped order, not an absent one).

Every inbound request is audited in ``webhook_logs`` (direction=inbound,
source="OMS") — inserted before processing and finalized with the outcome
afterwards, mirroring the QPMN inbound webhook handling in app/api/webhooks.py.
"""
import json
import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlmodel import Session, select

from app.core.auth_oms import verify_oms_auth
from app.core.config import settings
from app.core.database import get_session
from app.models.order import Order, OrderStatus
from app.models.order_cancel_record import OrderCancelRecord, OrderCancelRecordStatus
from app.models.webhook_log import WebhookDirection, WebhookLog, WebhookProcessStatus
from app.schemas.order import (
    OMSAmbiguousErrorDetail,
    OMSAmbiguousErrorResponse,
    OMSAmbiguousOrderCandidate,
    OMSOrderActionResponse,
    OMSOrderCancelRequest,
    OMSOrderReturnRequest,
    SiteFlowErrorResponse,
)
from app.services.order import (
    OrderNotCancellableError,
    OrderNotReturnableError,
    QPMNCancelError,
    QPMNReturnError,
    order_service,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/oms",
    dependencies=[Depends(verify_oms_auth)],
)


def _log_request(endpoint: str, request_body: dict):
    """Log request body when DEBUG is enabled."""
    if settings.DEBUG:
        logger.info(
            "[OMS] %s | Request: %s",
            endpoint,
            json.dumps(request_body, ensure_ascii=False, default=str),
        )


def _log_response(endpoint: str, response: dict):
    """Log response body when DEBUG is enabled."""
    if settings.DEBUG:
        logger.info(
            "[OMS] %s | Response: %s",
            endpoint,
            json.dumps(response, ensure_ascii=False, default=str),
        )


def _siteflow_error(message: str, name: str, code: int) -> HTTPException:
    """Build a SiteFlow-compatible HTTPException (same shape as the SiteFlow
    cancel endpoint in app/api/orders.py)."""
    error_resp = SiteFlowErrorResponse(
        success=False,
        error={"message": message, "name": name, "code": code},
    )
    return HTTPException(status_code=code, detail=error_resp.model_dump())


def _ambiguous_error(source_order_id: str, orders: List[Order]) -> HTTPException:
    """Build the 409 rejection for a sourceOrderId matching several active
    orders — the payload lists the candidates so OMS can escalate."""
    error_resp = OMSAmbiguousErrorResponse(
        error=OMSAmbiguousErrorDetail(
            message=(
                f"Multiple active orders share sourceOrderId '{source_order_id}'; "
                f"the request is ambiguous, resolve manually"
            ),
            name="Conflict",
            code=409,
            candidates=[
                OMSAmbiguousOrderCandidate(
                    order_id=o.order_id, store_id=o.store_id, status=o.status.value
                )
                for o in orders
            ],
        )
    )
    return HTTPException(status_code=409, detail=error_resp.model_dump())


def _find_active_orders_by_source_order_id(
    session: Session, source_order_id: str
) -> List[Order]:
    """All active orders carrying *source_order_id* (soft-deleted hidden)."""
    return list(
        session.exec(
            select(Order).where(
                Order.source_order_id == source_order_id,
                Order.is_active.is_(True),
            )
        ).all()
    )


def _record_pending_cancel(session: Session, source_order_id: str, reason: Optional[str]) -> None:
    """Remember an OMS cancel request that arrived before its order did.

    Idempotent per sourceOrderId: a retried deferred cancel refreshes the
    pending row's reason instead of stacking duplicates. The order_publishing
    worker consumes the record when the VFS-created order finally arrives and
    cancels it locally instead of publishing it.
    """
    existing = session.exec(
        select(OrderCancelRecord).where(
            OrderCancelRecord.source_order_id == source_order_id,
            OrderCancelRecord.status == OrderCancelRecordStatus.PENDING,
        )
    ).first()
    if existing:
        if reason:
            existing.reason = reason
        session.add(existing)
        session.commit()
        return

    session.add(OrderCancelRecord(source_order_id=source_order_id, reason=reason))
    session.commit()


def _insert_inbound_log(
    session: Session,
    source_order_id: str,
    event_status: str,
    payload: dict,
) -> WebhookLog:
    """Insert the inbound OMS audit row before processing (committed
    immediately so downstream rollbacks cannot lose it)."""
    log = WebhookLog(
        direction=WebhookDirection.INBOUND,
        source="OMS",
        order_id="",
        source_order_id=source_order_id,
        event_status=event_status,
        payload=payload,
        process_status=WebhookProcessStatus.RECEIVED,
    )
    session.add(log)
    session.commit()
    session.refresh(log)
    return log


def _finalize_inbound_log(
    session: Session,
    log: WebhookLog,
    ok: bool,
    details: dict | str,
    order: Optional[Order] = None,
) -> None:
    """Stamp the audit row with the processing outcome (and the order's ids
    once the order is known)."""
    log.process_status = WebhookProcessStatus.PROCESSED if ok else WebhookProcessStatus.FAILED
    log.details = (
        details if isinstance(details, str)
        else json.dumps(details, ensure_ascii=False, default=str)
    )
    if order is not None:
        log.order_id = order.order_id
        log.store_order_id = order.store_order_id
    log.updated_at = datetime.now(timezone.utc)
    session.add(log)
    session.commit()


def _error_message(exc: HTTPException) -> str:
    """Extract the human-readable message from a _siteflow_error-shaped
    HTTPException for the audit log."""
    detail = exc.detail
    if isinstance(detail, dict):
        error = detail.get("error") or {}
        if isinstance(error, dict) and error.get("message"):
            return str(error["message"])
    return str(detail)


@router.post("/order/{source_order_id}/cancel", response_model=OMSOrderActionResponse, tags=["Order"], summary="Cancel")
def oms_cancel_order(
    source_order_id: str,
    request: Optional[OMSOrderCancelRequest] = None,
    session: Session = Depends(get_session),
):
    """
    Cancel Order (OMS) - Cancels an order by sourceOrderId alone.

    Unlike the SiteFlow cancel endpoint this needs no source_account; OMS is
    a cross-store system. When several active orders share the
    sourceOrderId, the request is rejected with 409 listing the candidates.

    Cancelling an already-cancelled order is idempotent (200, no side
    effects) so OMS retries are always safe. A cancel for an order that
    has not arrived yet is likewise accepted (200): the request is
    recorded in order_cancel_list and applied automatically when the
    VFS-created order enters the publishing pipeline.

    Error responses follow the SiteFlow format:
    ``{"success": false, "error": {"message": "...", "name": "...", "code": ...}}``

    | HTTP Code | Error Name         | Scenario                                        |
    |-----------|--------------------|-------------------------------------------------|
    | 409       | Conflict           | Ambiguous sourceOrderId / status not cancellable |
    | 502       | BadGateway         | QPMN cancel API call failed                    |
    | 500       | InternalServerError| Unexpected internal error                      |
    """
    endpoint = "POST /api/oms/order/cancel"
    body = request.model_dump() if request else {}
    _log_request(endpoint, {"source_order_id": source_order_id, **body})
    inbound_log = _insert_inbound_log(
        session,
        source_order_id=source_order_id,
        event_status=OrderStatus.CANCELLED.value,
        payload={"sourceOrderId": source_order_id, "request": body},
    )
    try:
        orders = _find_active_orders_by_source_order_id(session, source_order_id)

        if not orders:
            # The cancel arrived before the order did (e.g. customer
            # cancelled in OMS before VFS's create request reached us) —
            # remember it so the publishing worker can cancel the order
            # the moment it arrives, and acknowledge to OMS that the
            # deferred cancellation was accepted.
            _record_pending_cancel(session, source_order_id, body.get("reason"))
            resp = OMSOrderActionResponse(
                success=True,
                message=(
                    "Order not found yet; cancellation recorded and will be "
                    "applied when the order arrives"
                ),
                source_order_id=source_order_id,
            )
            _finalize_inbound_log(session, inbound_log, ok=True, details=resp.model_dump())
            _log_response(endpoint, resp.model_dump())
            return resp

        if len(orders) > 1:
            raise _ambiguous_error(source_order_id, orders)

        order = orders[0]

        # Idempotent: an already-cancelled order is reported as success so a
        # retried cancel (e.g. lost response) never turns into an error.
        if order.status == OrderStatus.CANCELLED:
            resp = OMSOrderActionResponse(
                success=True,
                message="Order already cancelled",
                order_id=order.order_id,
                source_order_id=source_order_id,
                status=order.status.value,
            )
            _finalize_inbound_log(session, inbound_log, ok=True, details=resp.model_dump(), order=order)
            _log_response(endpoint, resp.model_dump())
            return resp

        if request and request.reason:
            order_service.append_log(
                order, "oms_cancel_requested",
                f"OMS requested cancellation: {request.reason}",
            )

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

        resp = OMSOrderActionResponse(
            success=True,
            message="Order cancelled successfully",
            order_id=order.order_id,
            source_order_id=source_order_id,
            status=order.status.value,
        )
        _finalize_inbound_log(session, inbound_log, ok=True, details=resp.model_dump(), order=order)
        _log_response(endpoint, resp.model_dump())
        return resp

    except HTTPException as exc:
        _finalize_inbound_log(session, inbound_log, ok=False, details=_error_message(exc))
        raise
    except Exception as e:
        session.rollback()
        _finalize_inbound_log(
            session, inbound_log, ok=False, details=f"Order cancellation failed: {str(e)}"
        )
        raise _siteflow_error(
            f"Order cancellation failed: {str(e)}",
            "InternalServerError",
            500,
        )


@router.post("/order/{source_order_id}/return", response_model=OMSOrderActionResponse, tags=["Order"], summary="Return")
def oms_return_order(
    source_order_id: str,
    request: OMSOrderReturnRequest,
    session: Session = Depends(get_session),
):
    """
    Return Order (OMS) - Initiates a return on a SHIPPED order.

    Calls the QPMN return API first (endpoint path pending QPMN
    confirmation, configurable via ``QPMN_RETURN_ENDPOINT_PATH``); only when
    QPMN accepts does the order move to ``returning`` — otherwise it stays
    ``shipped`` and the failure is reported to OMS. The completion
    transition (``returning`` -> ``returncompleted``) will be wired to
    QPMN's return feedback once that event is defined.

    Requesting a return on an order already ``returning`` /
    ``returncompleted`` is idempotent (200, no side effects).

    | HTTP Code | Error Name         | Scenario                                         |
    |-----------|--------------------|--------------------------------------------------|
    | 404       | NotFound           | No active order with the sourceOrderId           |
    | 409       | Conflict           | Ambiguous sourceOrderId / order not shipped      |
    | 502       | BadGateway         | QPMN return API call failed                      |
    | 500       | InternalServerError| Unexpected internal error                        |
    """
    endpoint = "POST /api/oms/order/return"
    body = request.model_dump()
    _log_request(endpoint, {"source_order_id": source_order_id, **body})
    inbound_log = _insert_inbound_log(
        session,
        source_order_id=source_order_id,
        event_status=OrderStatus.RETURNING.value,
        payload={"sourceOrderId": source_order_id, "request": body},
    )
    try:
        orders = _find_active_orders_by_source_order_id(session, source_order_id)

        if not orders:
            raise _siteflow_error(
                f"Order with sourceOrderId '{source_order_id}' not found",
                "NotFound",
                404,
            )

        if len(orders) > 1:
            raise _ambiguous_error(source_order_id, orders)

        order = orders[0]

        # Idempotent: an order already in (or past) the return flow is
        # reported as success so a retried return request never errors.
        if order.status in (OrderStatus.RETURNING, OrderStatus.RETURN_COMPLETED):
            resp = OMSOrderActionResponse(
                success=True,
                message="Order return already requested",
                order_id=order.order_id,
                source_order_id=source_order_id,
                status=order.status.value,
            )
            _finalize_inbound_log(session, inbound_log, ok=True, details=resp.model_dump(), order=order)
            _log_response(endpoint, resp.model_dump())
            return resp

        try:
            order = order_service.return_order(session, order, return_payload=body)
        except OrderNotReturnableError as exc:
            raise _siteflow_error(
                str(exc),
                "Conflict",
                409,
            )
        except QPMNReturnError as qpmn_err:
            raise _siteflow_error(
                str(qpmn_err),
                "BadGateway",
                502,
            )

        resp = OMSOrderActionResponse(
            success=True,
            message="Order return requested successfully",
            order_id=order.order_id,
            source_order_id=source_order_id,
            status=order.status.value,
        )
        _finalize_inbound_log(session, inbound_log, ok=True, details=resp.model_dump(), order=order)
        _log_response(endpoint, resp.model_dump())
        return resp

    except HTTPException as exc:
        _finalize_inbound_log(session, inbound_log, ok=False, details=_error_message(exc))
        raise
    except Exception as e:
        session.rollback()
        _finalize_inbound_log(
            session, inbound_log, ok=False, details=f"Order return failed: {str(e)}"
        )
        raise _siteflow_error(
            f"Order return failed: {str(e)}",
            "InternalServerError",
            500,
        )
