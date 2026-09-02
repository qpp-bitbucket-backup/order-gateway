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
from app.core.sentry_alerts import ALERTS, capture_integration_alert
from app.models.client import Client
from app.models.order import Order, OrderStatus, can_transition, is_item_event_superseded, EVENT_STATUS_MAP, OMS_STATUS_MAP
from app.models.shipment import OrderShipment
from app.models.webhook_log import WebhookLog, WebhookDirection, WebhookProcessStatus
from app.schemas.webhook import QpmnOrderItemEvent, QpmnPackageShippedEvent, WebhookResponse
from app.tasks.notifications import notify_oms, notify_vfs

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/api/webhook/qpmn",
    tags=["Webhooks"],
)

_WEBHOOK_INBOUND_ALERTS = ALERTS["webhook_inbound"]


def _capture_qpmn_alert(alert_key: str, order_id: Optional[str] = None, **format_args) -> None:
    """Capture an inbound QPMN webhook processing failure to Sentry.

    These are "soft" failures — the endpoint still returns HTTP 200 with
    ``success: false`` (per QPMN's expected ack shape), so nothing here ever
    raises and Sentry's FastAPI integration would never see them on its own.
    """
    capture_integration_alert(
        _WEBHOOK_INBOUND_ALERTS[alert_key], order_id=order_id,
        format_args={"order_id": order_id, **format_args}, component="qpmn_webhook_inbound",
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


def _notify_component_printed(
    session: Session, order: Order, item_id_value: Optional[str], x_qpmn_event_id: Optional[str],
) -> None:
    """TI-65: notify OMS/VFS of a single component's completion ("printed"),
    independent of whether this event also advances the order's own status —
    QPMN sends one order_item_produced event per item, and OMS wants to know
    about each one, not just the first (which is the only one the pre-TI-65
    flow ever notified, since every later item's event was silently SKIPPED
    as superseded).
    """
    oms_status = OMS_STATUS_MAP[OrderStatus.PRINTED]
    oms_log = WebhookLog(
        direction=WebhookDirection.OUTBOUND, source="OMS",
        order_id=order.order_id, source_order_id=order.source_order_id,
        store_order_id=order.store_order_id, store_order_item_id=item_id_value,
        event_id=x_qpmn_event_id, event_status="order_item_produced",
        payload={"orderNo": order.order_id, "status": oms_status, "shipments": []},
        process_status=WebhookProcessStatus.RECEIVED,
    )
    session.add(oms_log)
    session.commit()
    session.refresh(oms_log)
    notify_oms.delay(webhook_log_id=oms_log.id, order_id=order.order_id, event_status=oms_status, shipments=None)

    vfs_log = WebhookLog(
        direction=WebhookDirection.OUTBOUND, source="VFS",
        order_id=order.order_id, source_order_id=order.source_order_id,
        store_order_id=order.store_order_id, store_order_item_id=item_id_value,
        event_id=x_qpmn_event_id, event_status="order_item_produced",
        payload={"sourceOrderId": order.source_order_id, "status": OrderStatus.PRINTED.value, "shipments": []},
        process_status=WebhookProcessStatus.RECEIVED,
    )
    session.add(vfs_log)
    session.commit()
    session.refresh(vfs_log)
    notify_vfs.delay(webhook_log_id=vfs_log.id, order_id=order.order_id, event_status=OrderStatus.PRINTED.value, shipments=None)


def _record_produced_item_and_maybe_complete(
    session: Session, order: Order, item_id_value: Optional[str], x_qpmn_event_id: Optional[str],
) -> None:
    """TI-65: record ``item_id_value`` as produced and, once every item QPMN
    was pushed for this order (``order.store_order_item_ids``, captured from
    the create-order response — see ``_extract_store_order_item_ids`` in
    app/tasks/orders.py) has reported produced, advance the order to
    ``PRODUCED`` and notify OMS (only — not VFS) of that aggregate status.

    A no-op (order stays wherever it is) if ``store_order_item_ids`` was
    never captured (e.g. an order pushed before this feature existed) —
    there is no way to know the expected item count, so we simply never
    reach "all produced" for it.
    """
    if item_id_value:
        if order.produced_item_ids is None:
            order.produced_item_ids = []
        if item_id_value not in order.produced_item_ids:
            order.produced_item_ids = [*order.produced_item_ids, item_id_value]
            flag_modified(order, "produced_item_ids")
            session.add(order)
            session.commit()

    expected = set(order.store_order_item_ids or [])
    produced = set(order.produced_item_ids or [])
    if not expected or not expected.issubset(produced):
        return
    if not can_transition(order.status, OrderStatus.PRODUCED):
        # Already PRODUCED (or moved further, e.g. SHIPPED/CANCELLED) — nothing to do.
        return

    order.status = OrderStatus.PRODUCED
    if order.logs is None:
        order.logs = []
    order.logs.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": "qpmn_status_webhook",
        "message": "All order items produced — status updated to 'produced'",
        "event_status": "order_item_produced",
        "worker": "webhook_handler",
    })
    flag_modified(order, "logs")
    session.add(order)
    session.commit()

    produced_oms_status = OMS_STATUS_MAP[OrderStatus.PRODUCED]
    oms_log = WebhookLog(
        direction=WebhookDirection.OUTBOUND, source="OMS",
        order_id=order.order_id, source_order_id=order.source_order_id,
        store_order_id=order.store_order_id,
        event_id=x_qpmn_event_id, event_status="order_produced",
        payload={"orderNo": order.order_id, "status": produced_oms_status, "shipments": []},
        process_status=WebhookProcessStatus.RECEIVED,
    )
    session.add(oms_log)
    session.commit()
    session.refresh(oms_log)
    notify_oms.delay(webhook_log_id=oms_log.id, order_id=order.order_id, event_status=produced_oms_status, shipments=None)
    # OMS only — VFS is not notified of the aggregate "produced" status.


@router.post("/order-status", response_model=WebhookResponse)
async def receive_order_status(
    request: Request,
    session: Session = Depends(get_session),
    x_qpmn_event_type: Optional[str] = Header(None, alias="x-qpmn-event-type"),
    x_qpmn_event_id: Optional[str] = Header(None, alias="x-qpmn-event-id"),
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
    3. Dedup on ``x-qpmn-event-id`` (§5.6) — a retried delivery is acknowledged
       without being reprocessed.
    4. Parse the body per ``x-qpmn-event-type``: ``package_shipped`` (§5.4.2,
       a shipment object) or an ``order_item_*`` event (§5.4.1, an order item).
    5. Look up the order via the body's ``orderId`` and validate the transition.
    6. Update ``orders.status`` and append a log entry.
    7. Create outbound logs (``source=oms`` / ``source=vfs``) and enqueue
       ``notify_oms`` and ``notify_vfs``.
    """
    raw_body = await request.body()
    client = _verify_qpmn_signature(session, raw_body, x_qpmn_hmac_sha256)
    signature_valid = client is not None

    try:
        raw_payload = json.loads(raw_body) if raw_body else {}
    except json.JSONDecodeError:
        raw_payload = {}

    is_shipped_event = x_qpmn_event_type == "package_shipped"
    order_id_value: Optional[int] = raw_payload.get("orderId")
    item_id_value: Optional[str] = None if is_shipped_event else raw_payload.get("id")

    # Insert inbound log (raw payload, unchanged regardless of downstream outcome)
    inbound_log = WebhookLog(
        direction=WebhookDirection.INBOUND,
        source="QPMN",
        order_id="",
        store_order_id=str(order_id_value) if order_id_value is not None else None,
        store_order_item_id=str(item_id_value) if item_id_value is not None else None,
        event_id=x_qpmn_event_id,
        event_status=x_qpmn_event_type,
        payload=raw_payload,
        process_status=WebhookProcessStatus.RECEIVED,
    )
    session.add(inbound_log)
    session.commit()
    session.refresh(inbound_log)

    # Auth failure
    if not signature_valid:
        inbound_log.process_status = WebhookProcessStatus.FAILED
        inbound_log.details = "Invalid or missing x-qpmn-hmac-sha256 signature"
        inbound_log.updated_at = datetime.now(timezone.utc)
        session.add(inbound_log)
        session.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing x-qpmn-hmac-sha256 signature",
        )

    # Idempotency (§5.6): if this event_id already landed on an earlier
    # delivery, acknowledge without reprocessing — QPMN retries on anything
    # that doesn't come back 2xx fast enough, so the same event can arrive
    # more than once.
    if x_qpmn_event_id:
        duplicate = session.exec(
            select(WebhookLog).where(
                WebhookLog.direction == WebhookDirection.INBOUND,
                WebhookLog.source == "QPMN",
                WebhookLog.event_id == x_qpmn_event_id,
                WebhookLog.id != inbound_log.id,
            )
        ).first()
        if duplicate:
            response = WebhookResponse(
                success=True,
                message=f"Duplicate delivery of event_id '{x_qpmn_event_id}', already processed as log #{duplicate.id}",
            )
            inbound_log.process_status = WebhookProcessStatus.SKIPPED
            inbound_log.details = json.dumps(response.model_dump())
            inbound_log.updated_at = datetime.now(timezone.utc)
            session.add(inbound_log)
            session.commit()
            return response

    # x-qpmn-event-type must resolve to a known status; also parses the body
    # into the matching shape (order item vs. shipment) for downstream use.
    effective_status = x_qpmn_event_type
    new_status = EVENT_STATUS_MAP.get(effective_status) if effective_status else None
    if not new_status:
        response = WebhookResponse(success=False, message=f"Unmapped event type: '{effective_status}'")
        inbound_log.process_status = WebhookProcessStatus.SKIPPED
        inbound_log.details = json.dumps(response.model_dump())
        inbound_log.updated_at = datetime.now(timezone.utc)
        session.add(inbound_log)
        session.commit()
        _capture_qpmn_alert("UNMAPPED_EVENT_TYPE", event_type=effective_status)
        return response

    if is_shipped_event:
        event = QpmnPackageShippedEvent.model_validate(raw_payload)
        shipments_data = [event.model_dump()]
    else:
        event = QpmnOrderItemEvent.model_validate(raw_payload)
        shipments_data = [s.model_dump() for s in event.shipments] if event.shipments else None

    if not order_id_value:
        inbound_log.process_status = WebhookProcessStatus.FAILED
        inbound_log.details = "Missing orderId on event body"
        inbound_log.updated_at = datetime.now(timezone.utc)
        session.add(inbound_log)
        session.commit()
        _capture_qpmn_alert("MISSING_ORDER_ID", event_type=effective_status, item_id=item_id_value)
        return WebhookResponse(success=False, message="Missing orderId on event body")

    order = session.exec(
        select(Order).where(Order.store_order_id == str(order_id_value))
    ).first()

    if not order:
        inbound_log.process_status = WebhookProcessStatus.FAILED
        inbound_log.details = "Order not found"
        inbound_log.updated_at = datetime.now(timezone.utc)
        session.add(inbound_log)
        session.commit()
        _capture_qpmn_alert("ORDER_NOT_FOUND", qpmn_order_id=order_id_value, event_type=effective_status)
        return WebhookResponse(
            success=False,
            message="Order not found",
        )

    inbound_log.order_id = order.order_id
    inbound_log.source_order_id = order.source_order_id

    if not can_transition(order.status, new_status):
        if not is_shipped_event and is_item_event_superseded(order.status, new_status):
            # TI-65: a superseded order_item_produced still represents a real
            # component finishing — record it and notify OMS "printed" for
            # this component, even though the order itself already moved on
            # (via another item). May also complete the order to PRODUCED if
            # this was the last outstanding item.
            if effective_status == "order_item_produced":
                _notify_component_printed(session, order, event.id, x_qpmn_event_id)
                _record_produced_item_and_maybe_complete(session, order, event.id, x_qpmn_event_id)

            response = WebhookResponse(
                success=True,
                message=f"Item status recorded; order already at '{order.status.value}', not moved back to '{new_status.value}'",
            )
            inbound_log.process_status = WebhookProcessStatus.SKIPPED
            inbound_log.details = json.dumps(response.model_dump())
            inbound_log.updated_at = datetime.now(timezone.utc)
            session.add(inbound_log)
            session.commit()
            return response

        response = WebhookResponse(
            success=False,
            message=f"Invalid transition: {order.status.value} -> {new_status.value}",
        )
        inbound_log.process_status = WebhookProcessStatus.SKIPPED
        inbound_log.details = json.dumps(response.model_dump())
        inbound_log.updated_at = datetime.now(timezone.utc)
        session.add(inbound_log)
        session.commit()
        _capture_qpmn_alert(
            "INVALID_TRANSITION", order.order_id,
            from_status=order.status.value, to_status=new_status.value,
        )
        return response

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

    # TI-65: record this (first) produced item too, and complete straight to
    # PRODUCED if the order only ever had one item — the generic notify block
    # below already sends the per-component "printed" notification for it,
    # so we don't call _notify_component_printed here (would double-send).
    if effective_status == "order_item_produced":
        _record_produced_item_and_maybe_complete(session, order, event.id, x_qpmn_event_id)

    # Persist shipment tracking info for package_shipped events — a real,
    # queryable record instead of leaving it only inside webhook_logs.payload.
    if is_shipped_event:
        ship_date_dt = None
        if event.shipDate is not None:
            ship_date_dt = datetime.fromtimestamp(event.shipDate / 1000, tz=timezone.utc)
        shipment_record = OrderShipment(
            order_id=order.order_id,
            store_order_id=order.store_order_id,
            qpmn_shipment_id=str(event.id) if event.id is not None else None,
            tracking_number=event.trackingNumber,
            tracking_url=event.trackingUrl,
            carrier=event.company,
            ship_date=ship_date_dt,
            items=[i.model_dump() for i in event.items] if event.items else None,
        )
        session.add(shipment_record)
        session.commit()

    # Mark inbound log as processed
    response = WebhookResponse(success=True, message=f"Status updated to '{new_status.value}'")
    inbound_log.process_status = WebhookProcessStatus.PROCESSED
    inbound_log.details = json.dumps(response.model_dump())
    inbound_log.updated_at = datetime.now(timezone.utc)
    session.add(inbound_log)
    session.commit()

    # Create outbound log (source=oms) + enqueue notify_oms
    oms_status = OMS_STATUS_MAP[new_status]
    oms_outbound_log = WebhookLog(
        direction=WebhookDirection.OUTBOUND,
        source="OMS",
        order_id=order.order_id,
        source_order_id=order.source_order_id,
        store_order_id=order.store_order_id,
        store_order_item_id=str(item_id_value) if item_id_value is not None else None,
        event_id=x_qpmn_event_id,
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
        source="VFS",
        order_id=order.order_id,
        source_order_id=order.source_order_id,
        store_order_id=order.store_order_id,
        store_order_item_id=str(item_id_value) if item_id_value is not None else None,
        event_id=x_qpmn_event_id,
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

    return response
