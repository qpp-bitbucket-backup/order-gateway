"""QPMN order status webhook endpoints."""
import hashlib
import hmac
import json
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
from app.schemas.webhook import QpmnOrderItemEvent, QpmnPackageShippedEvent, WebhookResponse
from app.tasks.notifications import notify_oms, notify_vfs

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/webhook/qpmn",
    tags=["Webhooks"],
)


def _verify_qpmn_signature(session: Session, raw_body: bytes, signature: Optional[str]) -> Optional[Client]:
    """Verify ``x-qpmn-hmac-sha256`` against ``hex(HMAC-SHA256(key=store_token, message=raw_body))``.

    Per the QPMN webhook push spec (§5.5), the store token used as the HMAC
    key is the same one issued for calling QPMN's own open API — stored
    per-client in ``Client.store_key``. The push body carries no store
    identifier (only the optional, unverified ``x-qpmn-store-domain``
    header), so the matching client is found by trying every active
    client's key — fine at our current client count, revisit if it grows
    large enough to matter. Returns the matched ``Client`` on success,
    ``None`` on failure.
    """
    if not signature:
        return None
    clients = session.exec(select(Client).where(Client.is_active == True)).all()
    for client in clients:
        if not client.store_key:
            continue
        expected = hmac.new(client.store_key.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, signature.lower()):
            return client
    return None


@router.post("/order-status", response_model=WebhookResponse)
async def receive_order_status(
    request: Request,
    session: Session = Depends(get_session),
    x_qpmn_event_type: Optional[str] = Header(None, alias="x-qpmn-event-type"),
    x_qpmn_hmac_sha256: Optional[str] = Header(None, alias="x-qpmn-hmac-sha256"),
):
    """
    Receive a QPMN webhook push (order item status change or shipment), per
    docs/【toops接口设计】 - 对外.md §5.

    Each event is delivered as its own request — ``x-qpmn-event-type``
    (§5.3) says which of the two body shapes (§5.4) it is, there's no
    Printful-style bundled envelope.

    Processing order:
    1. Insert an inbound ``webhook_log`` (raw payload + headers).
    2. Verify the ``x-qpmn-hmac-sha256`` signature (result recorded on the log).
    3. Parse the body per ``x-qpmn-event-type``: ``package_shipped`` (§5.4.2,
       a shipment object) or an ``order_item_*`` event (§5.4.1, an order item).
    4. Look up the order via the body's ``orderId`` and validate the transition.
    5. Update ``orders.status`` and append a log entry.
    6. Create outbound logs (``source=oms`` / ``source=vfs``) and enqueue
       ``notify_oms`` and ``notify_vfs``.
    """
    raw_body = await request.body()
    headers_dict = {k: v for k, v in request.headers.items()}
    client = _verify_qpmn_signature(session, raw_body, x_qpmn_hmac_sha256)
    signature_valid = client is not None

    try:
        raw_payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        raw_payload = {}

    is_shipped_event = x_qpmn_event_type == "package_shipped"
    order_id_value: Optional[int] = raw_payload.get("orderId")

    # Insert inbound log (raw payload, unchanged regardless of downstream outcome)
    inbound_log = WebhookLog(
        direction=WebhookDirection.INBOUND,
        source="qpmn",
        order_id="",
        store_order_id=str(order_id_value) if order_id_value is not None else None,
        event_status=x_qpmn_event_type,
        payload=raw_payload,
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

    # x-qpmn-event-type must resolve to a known status; also parses the body
    # into the matching shape (order item vs. shipment) for downstream use.
    effective_status = x_qpmn_event_type
    new_status = EVENT_STATUS_MAP.get(effective_status) if effective_status else None
    if not new_status:
        inbound_log.process_status = WebhookProcessStatus.SKIPPED
        inbound_log.error_message = f"Unmapped event type: {effective_status}"
        session.add(inbound_log)
        session.commit()
        return WebhookResponse(success=False, message=f"Unmapped event type: '{effective_status}'")

    if is_shipped_event:
        event = QpmnPackageShippedEvent.model_validate(raw_payload)
        shipments_data = [event.model_dump()]
    else:
        event = QpmnOrderItemEvent.model_validate(raw_payload)
        shipments_data = [s.model_dump() for s in event.shipments] if event.shipments else None

    if not order_id_value:
        inbound_log.process_status = WebhookProcessStatus.FAILED
        inbound_log.error_message = "Missing orderId on event body"
        session.add(inbound_log)
        session.commit()
        return WebhookResponse(success=False, message="Missing orderId on event body")

    order = session.exec(
        select(Order).where(Order.store_order_id == str(order_id_value))
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
