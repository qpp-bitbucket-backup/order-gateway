"""QPMN order status webhook endpoints."""
import hmac
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from app.core.config import settings
from app.core.database import get_session
from app.models.order import Order, can_transition, EVENT_STATUS_MAP
from app.models.webhook_log import WebhookLog, WebhookDirection, WebhookProcessStatus
from app.schemas.webhook import QpmnStatusWebhookRequest, WebhookResponse
from app.tasks.notifications import notify_oms

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/webhook/qpmn",
    tags=["Webhooks"],
)


def _verify_qpmn_token(authorization: Optional[str]) -> bool:
    """Verify the ``Authorization: Basic {token}`` header against the configured token."""
    if not authorization:
        return False
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "basic":
        return False
    token = parts[1].strip()
    if not token or not settings.QPMN_WEBHOOK_TOKEN:
        return False
    return hmac.compare_digest(token, settings.QPMN_WEBHOOK_TOKEN)


@router.post("/order-status", response_model=WebhookResponse)
def receive_order_status(
    payload: QpmnStatusWebhookRequest,
    request: Request,
    session: Session = Depends(get_session),
    authorization: Optional[str] = Header(None),
):
    """
    Receive a QPMN/Popprint order status webhook.

    Processing order:
    1. Insert an inbound ``webhook_log`` (raw payload + headers).
    2. Verify the Basic token (result recorded on the log).
    3. Look up the order (``orderId`` → ``storeOrderId`` fallback).
    4. Validate the status transition.
    5. Update ``orders.status`` and append a log entry.
    6. Create an outbound log (``source=oms``) and enqueue ``notify_oms``.
    """
    signature_valid = _verify_qpmn_token(authorization)
    headers_dict = {k: v for k, v in request.headers.items()}

    # ① Insert inbound log
    inbound_log = WebhookLog(
        direction=WebhookDirection.INBOUND,
        source="qpmn",
        order_id=payload.orderId,
        source_order_id=payload.storeOrderId,
        event_status=payload.status,
        payload=payload.model_dump(),
        headers=headers_dict,
        signature_valid=signature_valid,
        process_status=WebhookProcessStatus.RECEIVED,
    )
    session.add(inbound_log)
    session.commit()
    session.refresh(inbound_log)

    # ② Auth failure
    if not signature_valid:
        inbound_log.process_status = WebhookProcessStatus.FAILED
        inbound_log.error_message = "Invalid or missing Authorization token"
        session.add(inbound_log)
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing Authorization token",
        )

    # ③ Look up order
    order = None
    if payload.orderId:
        order = session.exec(
            select(Order).where(Order.order_id == payload.orderId)
        ).first()
    if not order and payload.storeOrderId:
        order = session.exec(
            select(Order).where(Order.store_order_id == payload.storeOrderId)
        ).first()

    if not order:
        inbound_log.process_status = WebhookProcessStatus.FAILED
        inbound_log.error_message = "Order not found"
        session.add(inbound_log)
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Order not found",
        )

    inbound_log.order_id = order.order_id
    inbound_log.source_order_id = order.store_order_id

    # ④ Map external status → internal; unknown statuses are skipped
    new_status = EVENT_STATUS_MAP.get(payload.status)
    if not new_status:
        inbound_log.process_status = WebhookProcessStatus.SKIPPED
        inbound_log.error_message = f"Unmapped status: {payload.status}"
        session.add(inbound_log)
        session.commit()
        return WebhookResponse(success=True, message=f"Skipped: unmapped status '{payload.status}'")

    if not can_transition(order.status, new_status):
        inbound_log.process_status = WebhookProcessStatus.SKIPPED
        inbound_log.error_message = f"Invalid transition: {order.status.value} -> {new_status.value}"
        session.add(inbound_log)
        session.commit()
        return WebhookResponse(
            success=True,
            message=f"Skipped: invalid transition {order.status.value} -> {new_status.value}",
        )

    # ⑤ Update order status + append log
    order.status = new_status
    if order.logs is None:
        order.logs = []
    order.logs.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "qpmn_status_webhook",
        "message": f"Status updated to {new_status.value} via QPMN webhook",
        "event_status": payload.status,
        "worker": "webhook_handler",
    })
    flag_modified(order, "logs")
    session.add(order)
    session.commit()

    # Mark inbound log as processed
    inbound_log.process_status = WebhookProcessStatus.PROCESSED
    inbound_log.processed_at = datetime.now(timezone.utc)
    session.add(inbound_log)
    session.commit()

    # ⑥ Create outbound log (source=oms) + enqueue notify_oms
    shipments_data = (
        [s.model_dump() for s in payload.shipments] if payload.shipments else None
    )
    outbound_log = WebhookLog(
        direction=WebhookDirection.OUTBOUND,
        source="oms",
        order_id=order.order_id,
        source_order_id=order.store_order_id,
        event_status=payload.status,
        payload={
            "orderNo": order.order_id,
            "status": payload.status,
            "shipments": shipments_data or [],
        },
        process_status=WebhookProcessStatus.RECEIVED,
    )
    session.add(outbound_log)
    session.commit()
    session.refresh(outbound_log)

    notify_oms.delay(
        webhook_log_id=outbound_log.id,
        order_id=order.order_id,
        event_status=payload.status,
        shipments=shipments_data,
    )

    return WebhookResponse(success=True)
