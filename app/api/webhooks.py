"""QPMN order status webhook endpoints."""
import hashlib
import hmac
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm.attributes import flag_modified
from sqlmodel import Session, select

from app.core.database import get_session
from app.models.client import Client
from app.models.order import Order, can_transition, EVENT_STATUS_MAP, OMS_STATUS_MAP
from app.models.webhook_log import WebhookLog, WebhookDirection, WebhookProcessStatus
from app.schemas.webhook import QpmnStatusWebhookRequest, WebhookResponse
from app.tasks.notifications import notify_oms, notify_vfs

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/webhook/qpmn",
    tags=["Webhooks"],
)


def _verify_qpmn_signature(
    session: Session, store_id: str, raw_body: bytes, signature: Optional[str]
) -> Optional[Client]:
    """Verify ``x-qpmn-hmac-sha256`` against ``hex(HMAC-SHA256(key=store_token, message=raw_body))``.

    Per the QPMN webhook push spec (§5.5), the store token used as the HMAC
    key is the same one issued for calling QPMN's own open API — stored
    per-client in ``Client.store_key``. Returns the matched (active)
    ``Client`` on success, ``None`` on failure.
    """
    if not signature:
        return None
    client = session.exec(
        select(Client).where(Client.store_id == store_id, Client.is_active == True)
    ).first()
    if not client or not client.store_key:
        return None
    expected = hmac.new(client.store_key.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature.lower()):
        return None
    return client


@router.post("/order-status", response_model=WebhookResponse)
async def receive_order_status(
    payload: QpmnStatusWebhookRequest,
    request: Request,
    session: Session = Depends(get_session),
    x_qpmn_hmac_sha256: Optional[str] = Header(None, alias="x-qpmn-hmac-sha256"),
):
    """
    Receive a QPMN order_updated webhook (aligned with Printful's Webhook-API shape).

    Processing order:
    1. Insert an inbound ``webhook_log`` (raw payload + headers) — always the
       full raw payload, item-level statuses are never persisted separately.
    2. Verify the ``x-qpmn-hmac-sha256`` signature (result recorded on the log).
    3. Look up the order (``data.order.order_id`` → ``data.order.external_id`` fallback).
    4. Derive the effective status from ``data.order.items`` (whichever item's
       status changed drives the order status) and validate the transition.
    5. Update ``orders.status`` and append a log entry.
    6. Create outbound logs (``source=oms`` / ``source=vfs``) and enqueue
       ``notify_oms`` and ``notify_vfs``.
    """
    raw_body = await request.body()
    client = _verify_qpmn_signature(session, payload.store_id, raw_body, x_qpmn_hmac_sha256)
    signature_valid = client is not None
    headers_dict = {k: v for k, v in request.headers.items()}

    order_event = payload.data.order
    # Order status follows the item status: normally every item in one call
    # shares the same status. If they don't (mixed item statuses in a single
    # payload), fall back to the last item as the effective one — an edge
    # case we don't have real QPMN traffic for yet.
    item_statuses = {item.status for item in order_event.items}
    effective_status = item_statuses.pop() if len(item_statuses) == 1 else order_event.items[-1].status

    # Insert inbound log (raw payload, unchanged regardless of downstream outcome)
    inbound_log = WebhookLog(
        direction=WebhookDirection.INBOUND,
        source="qpmn",
        order_id="",
        source_order_id=order_event.external_id,
        store_order_id=order_event.order_id,
        event_status=effective_status,
        payload=payload.model_dump(),
        headers=headers_dict,
        signature_valid=signature_valid,
        process_status=WebhookProcessStatus.RECEIVED,
    )
    session.add(inbound_log)
    session.commit()
    session.refresh(inbound_log)

    # Auth failure
    if not signature_valid:
        inbound_log.process_status = WebhookProcessStatus.FAILED
        inbound_log.error_message = "Invalid or missing x-qpmn-hmac-sha256 signature"
        session.add(inbound_log)
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing x-qpmn-hmac-sha256 signature",
        )

    # Look up order: QPMN's own order_id (→ store_order_id) first, then our
    # source_order_id (echoed back as external_id) as fallback.
    order = None
    if order_event.order_id:
        order = session.exec(
            select(Order).where(Order.store_order_id == order_event.order_id)
        ).first()
    if not order and order_event.external_id:
        order = session.exec(
            select(Order).where(Order.source_order_id == order_event.external_id)
        ).first()

    if not order:
        inbound_log.process_status = WebhookProcessStatus.FAILED
        inbound_log.error_message = "Order not found"
        session.add(inbound_log)
        session.commit()
        return WebhookResponse(
            success=False,
            message="Order not found",
        )

    inbound_log.order_id = order.order_id

    # Map external status → internal; unknown statuses are rejected (success: false, HTTP 200)
    new_status = EVENT_STATUS_MAP.get(effective_status)
    if not new_status:
        inbound_log.process_status = WebhookProcessStatus.SKIPPED
        inbound_log.error_message = f"Unmapped status: {effective_status}"
        session.add(inbound_log)
        session.commit()
        return WebhookResponse(success=False, message=f"Unmapped status: '{effective_status}'")

    if not can_transition(order.status, new_status):
        inbound_log.process_status = WebhookProcessStatus.SKIPPED
        inbound_log.error_message = f"Invalid transition: {order.status.value} -> {new_status.value}"
        session.add(inbound_log)
        session.commit()
        return WebhookResponse(
            success=False,
            message=f"Invalid transition: {order.status.value} -> {new_status.value}",
        )

    # Update order status + append log
    order.status = new_status
    if order.logs is None:
        order.logs = []
    order.logs.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "qpmn_status_webhook",
        "message": f"Status updated to {new_status.value} via QPMN webhook",
        "event_status": effective_status,
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

    # Create outbound log (source=oms) + enqueue notify_oms
    shipments_data = (
        [s.model_dump() for s in order_event.shipments] if order_event.shipments else None
    )
    oms_status = OMS_STATUS_MAP[new_status]
    oms_outbound_log = WebhookLog(
        direction=WebhookDirection.OUTBOUND,
        source="oms",
        order_id=order.order_id,
        source_order_id=order.source_order_id,
        store_order_id=order.store_order_id,
        event_status=effective_status,
        payload={
            "orderNo": order.order_id,
            "status": oms_status,
            "shipments": shipments_data or [],
        },
        process_status=WebhookProcessStatus.RECEIVED,
    )
    session.add(oms_outbound_log)
    session.commit()
    session.refresh(oms_outbound_log)

    notify_oms.delay(
        webhook_log_id=oms_outbound_log.id,
        order_id=order.order_id,
        event_status=oms_status,
        shipments=shipments_data,
    )

    # Outbound log (source=vfs) + enqueue notify_vfs (SiteFlow-style postback)
    vfs_outbound_log = WebhookLog(
        direction=WebhookDirection.OUTBOUND,
        source="vfs",
        order_id=order.order_id,
        source_order_id=order.source_order_id,
        store_order_id=order.store_order_id,
        event_status=effective_status,
        payload={
            "sourceOrderId": order.source_order_id,
            "status": new_status.value,
            "shipments": shipments_data or [],
        },
        process_status=WebhookProcessStatus.RECEIVED,
    )
    session.add(vfs_outbound_log)
    session.commit()
    session.refresh(vfs_outbound_log)

    notify_vfs.delay(
        webhook_log_id=vfs_outbound_log.id,
        order_id=order.order_id,
        event_status=new_status.value,
        shipments=shipments_data,
    )

    return WebhookResponse(success=True, message=f"Status updated to '{new_status.value}'")
