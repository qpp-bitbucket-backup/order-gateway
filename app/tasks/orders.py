"""Order processing Celery tasks."""
import json
import logging
import tempfile
import httpx
import sentry_sdk
from typing import Dict, Any, List
from datetime import datetime, timezone
from sqlmodel import Session, select
from sqlalchemy import or_
from sqlalchemy.orm import attributes
from app.core.celery import celery_app
from app.core.database import engine
from app.core.config import settings
from app.core.rabbitmq import QUEUE_ORDER_PUBLISHING, QUEUE_ORDER_VALIDATING, QUEUE_ORDER_PUSHING
from app.models.order import Order, OrderStatus, OrderType, can_transition, OMS_STATUS_MAP
from app.models.address import Address, AddressType
from app.models.product import Sku
from app.models.webhook_log import WebhookLog, WebhookDirection, WebhookProcessStatus
from app.core.sentry_alerts import ALERTS, capture_integration_alert
from app.services.file import file_service
from app.services.watermark import watermark_to_png, WATERMARK_TEXT
from app.services.client import client_service
from app.services.oms import oms_service, OMSRetryableError

logger = logging.getLogger(__name__)

# When DEBUG is enabled, emit this module's debug logs (e.g. QPMN push
# payload/response dumps) even under the worker's default INFO level.
if settings.DEBUG:
    logger.setLevel(logging.DEBUG)

_PUSH_ALERTS = ALERTS["push"]


def _capture_push_alert(alert_key: str, order_id: str, **format_args) -> None:
    """Capture a push_order failure to Sentry, tagged for alert-rule filtering.

    ``order_queue=order_pushing`` matches QUEUE_ORDER_PUSHING, letting Sentry
    alert rules target this queue specifically.
    """
    capture_integration_alert(
        _PUSH_ALERTS[alert_key],
        order_id=order_id,
        format_args={"order_id": order_id, **format_args},
        order_queue=QUEUE_ORDER_PUSHING,
    )


def _exponential_backoff(base: int, retry_count: int, cap: int) -> int:
    """Calculate exponential backoff delay: min(base * 2^retry_count, cap)."""
    return min(base * (2 ** retry_count), cap)


def _extract_store_order_item_ids(data: Any) -> List[str]:
    """Pull the QPMN-assigned id of every pushed order item out of the
    create-order response's ``data``, so later ``order_item_produced``
    webhooks (keyed by these same ids) can be matched against the full set
    to detect "all items produced" (TI-65).

    Legacy API: ``data.orderItems[].orderItemId``.
    Open API: ``data.items[].id``.
    """
    if not isinstance(data, dict):
        return []
    item_ids: List[str] = []
    for item in data.get("orderItems") or data.get("items") or []:
        if not isinstance(item, dict):
            continue
        item_id = item.get("orderItemId") or item.get("id")
        if item_id is not None:
            item_ids.append(str(item_id))
    return item_ids


_ADDRESS_FIELDS = (
    "country", "state", "city", "address1", "address2", "postcode",
    "first_name", "last_name", "phone", "mobile", "email", "company",
)


def _attach_addresses_to_order(
    session: Session, order: Order, fetched: Dict[str, Any]
) -> Dict[str, Any]:
    """
    Copy freshly fetched/base addresses onto *order*, keyed by its internal
    order_id — the push payload lookup (``Address.order_id == order.order_id``)
    only finds rows keyed that way, while OMS rows are keyed by the orderNo
    used for the fetch. Replaces any rows previously attached to this order.
    """
    for existing in session.exec(
        select(Address).where(Address.order_id == order.order_id)
    ).all():
        session.delete(existing)

    attached: Dict[str, Any] = {}
    for key in ("delivery", "billing"):
        src = fetched.get(key)
        if src is None:
            continue
        attached[key] = Address(
            **{field: getattr(src, field) for field in _ADDRESS_FIELDS},
            order_id=order.order_id,
            type=src.type,
        )
        session.add(attached[key])
    session.commit()
    return attached


def _resolve_parallel_card_addresses(session: Session, order: Order) -> Dict[str, Any]:
    """
    Resolve addresses for a parallel-card order.

    Parallel cards ship to the base card's address:
    1. reuse the base order's stored addresses when present (copied onto
       this order);
    2. otherwise fetch from OMS with the base prefix — OMS only knows the
       base orderNo, never the parallel card's full source order id.
    """
    from app.services.order import order_service, parse_parallel_card_id

    parent = order_service.find_parallel_card_parent(
        session, order.source_order_id, store_id=order.store_id
    )
    if parent:
        # Base-card rows are keyed by the parent's source_order_id (the
        # current fetch key); older rows may still be keyed by the parent UUID.
        rows = session.exec(
            select(Address).where(
                Address.order_id.in_([parent.order_id, parent.source_order_id])
            )
        ).all()
        fetched = {
            "delivery": next((a for a in rows if a.type == AddressType.DELIVERY), None),
            "billing": next((a for a in rows if a.type == AddressType.BILLING), None),
        }
        if fetched["delivery"]:
            if not fetched["billing"]:
                # Base stored delivery only — ask OMS for the missing billing
                # with the base prefix (same orderNo a full fetch would use),
                # otherwise the push payload falls back to billing == delivery.
                parsed = parse_parallel_card_id(order.source_order_id)
                prefix = parsed[0] if parsed else order.source_order_id
                logger.info(
                    f"[Celery] Base-card {parent.source_order_id} has no billing "
                    f"address for parallel card {order.order_id}, fetching from "
                    f"OMS with prefix {prefix}"
                )
                oms_fetched = oms_service.fetch_order_addresses(prefix, session)
                fetched["billing"] = oms_fetched.get("billing")
            logger.info(
                f"[Celery] Reusing base-card {parent.source_order_id} addresses "
                f"for parallel card {order.order_id}"
            )
            return _attach_addresses_to_order(session, order, fetched)

    parsed = parse_parallel_card_id(order.source_order_id)
    prefix = parsed[0] if parsed else order.source_order_id
    logger.info(
        f"[Celery] No base-card addresses for parallel card {order.order_id}, "
        f"fetching from OMS with prefix {prefix}"
    )
    fetched = oms_service.fetch_order_addresses(prefix, session)
    return _attach_addresses_to_order(session, order, fetched)


@celery_app.task(bind=True, name="tasks.orders.publish_order", queue=QUEUE_ORDER_PUBLISHING)
def publish_order(self, order_data: Dict[str, Any]) -> bool:
    """
    Publish (process) an order: download files, upload to QPMN,
    then chain to validate_order.

    Args:
        order_data: Order data dictionary.

    Returns:
        True if successful, False otherwise.
    """
    order_id = order_data.get("order_id")
    logger.info(f"[Celery] Publishing order task: order_id={order_id}")

    try:
        with Session(engine) as session:
            order = session.exec(
                select(Order).where(Order.order_id == order_id)
            ).first()

            if not order:
                logger.error(f"[Celery] Order not found: {order_id}")
                return False

            if not can_transition(order.status, OrderStatus.PENDING):
                logger.error(
                    f"[Celery] Invalid state transition for order {order_id}: "
                    f"{order.status.value} -> {OrderStatus.PENDING.value}"
                )
                return False

            order.status = OrderStatus.PENDING

            _append_order_log(order, "order_publishing_started", "Order publishing started by celery worker")

            session.add(order)
            session.commit()
            logger.info(f"[Celery] Order {order_id} status updated to PENDING")

            uploaded_files: Dict = {}
            products = order.order_data.get("items", [])
            store_id = order.store_id
            store_key = client_service.get_store_key_by_id(store_id)
            # Parallel-card orders: every design file must reach QPMN as a
            # PNG stamped with the "Topps Now 客供產品" watermark (PDFs are
            # split, then stamped and rasterized; images are stamped directly).
            is_parallel_card = order.type == OrderType.PARALLEL_CARD
            file_quantity = 0
            file_index = 0
            # PDF->PNG converted page count (CONVERT_TO_PNG), logged once
            # alongside the upload summary below
            png_file_count = 0
            with tempfile.TemporaryDirectory(prefix=f"order_{order_id}_") as tmp_dir:
                for product in products:
                    components = product.get("components", [])
                    sku = product.get("sku", None)
                    # Resolve items[].sku (source_sku or internal id) to the
                    # internal sku_id so order.files keys always use the
                    # original QPMN id
                    if sku:
                        sku_row = session.exec(
                            select(Sku).where(or_(Sku.sku_id == sku, Sku.source_sku == sku))
                        ).first()
                        if sku_row:
                            sku = sku_row.sku_id
                    for component in components:
                        file_url = component.get("path", None)
                        if not file_url:
                            file_index +=1
                            continue

                        logger.info(f"[Celery] Processing SKU [{sku}] file: {file_url}")
                        item_files = []
                        try:
                            success, result = file_service.download_file(file_url, tmp_dir)
                            if not success:
                                _mark_order_failed(order_id,"Cannot download design files")
                                logger.error(f"File: [{file_url}] download failed") 
                                return True

                            if result.lower().endswith(".pdf"):
                                page_files = file_service.split_pdf(result, tmp_dir)
                                if not page_files:
                                    _mark_order_failed(order_id,"Cannot split PDF file")
                                    logger.error(f"File: [{file_url}] PDF split failed") 
                                    return True
                            elif result.lower().endswith((".jpg", ".jpeg", ".png")):
                                page_files = [result]
                            else:
                                _mark_order_failed(order_id, f"Unsupported file format: {file_url}")
                                logger.error(f"File: [{file_url}] unsupported format, only pdf/jpg/png are supported")
                                return True
                            # CONVERT_TO_PNG: render the split single-page
                            # PDFs to PNG before upload; image files pass
                            # through unchanged (parallel-card files are
                            # watermarked to PNG below regardless)
                            if settings.CONVERT_TO_PNG:
                                converted_files: List[str] = []
                                for page_file in page_files:
                                    if not page_file.lower().endswith(".pdf"):
                                        converted_files.append(page_file)
                                        continue
                                    png_path = file_service.pdf_to_png(page_file, tmp_dir)
                                    if not png_path:
                                        _mark_order_failed(order_id, "Cannot convert design PDF to PNG")
                                        logger.error(f"File: [{page_file}] PDF to PNG conversion failed")
                                        return True
                                    converted_files.append(png_path)
                                    png_file_count += 1
                                page_files = converted_files

                            for page_file in page_files:
                                if is_parallel_card:
                                    try:
                                        page_file = watermark_to_png(page_file, tmp_dir)
                                    except Exception as wm_err:
                                        _mark_order_failed(order_id, f"Cannot watermark design files: {wm_err}")
                                        logger.error(f"File: [{page_file}] watermark failed: {wm_err}", exc_info=True)
                                        return True
                                upload_result = file_service.upload_to_qpmn(page_file, store_key)
                                if not upload_result:
                                    _mark_order_failed(order_id,"Cannot upload design files")
                                    logger.error(f"File: [{page_file}] upload failed")
                                    return True
                                item_files.append(upload_result)
                            uploaded_files.setdefault(f"{sku}-{file_index}", []).extend(item_files)
                            # uploaded_files[f"{sku}-{file_index}"] += item_files
                            
                        except Exception as file_err:
                            logger.error(
                                f"[Celery] Failed to process file {file_url}: {file_err}",
                                exc_info=True,
                            )
                    file_index+=1
                    file_quantity += len(item_files)
                if uploaded_files:
                    order.files = uploaded_files
                    if is_parallel_card:
                        _append_order_log(
                            order,
                            "parallel_card_watermarked",
                            f"Applied '{WATERMARK_TEXT}' watermark to {file_quantity} file(s) before QPMN upload",
                        )
                    if png_file_count:
                        _append_order_log(
                            order,
                            "design_files_converted_to_png",
                            f"Converted {png_file_count} design PDF page(s) to PNG "
                            f"({settings.PDF_TO_PNG_DPI} dpi) before QPMN upload",
                        )
                    session.add(order)
                    session.commit()
                    logger.info(
                        f"[Celery] Saved {file_quantity} file(s) to order {order_id}"
                    )

            # Chain to validate_order
            task_payload = {
                "order_id": order.order_id,
                "source_order_id": order.source_order_id,
                "status": order.status.value,
                "created_at": order.created_at.isoformat() if order.created_at else None,
            }
            validate_order.apply_async(args=[task_payload])
            return True

    except Exception as e:
        logger.error(f"[Celery] Failed to publish order {order_id}: {e}", exc_info=True)
        _mark_order_failed(order_id, str(e))
        return False


@celery_app.task(bind=True, name="tasks.orders.validate_order", queue=QUEUE_ORDER_VALIDATING)
def validate_order(self, order_data: Dict[str, Any]) -> bool:
    """
    Validate an order: fetch addresses from OMS, then chain to push_order.

    Args:
        order_data: Order data dictionary.

    Returns:
        True if successful, False otherwise.
    """
    order_id = order_data.get("order_id")
    logger.info(f"[Celery] Validating order task: order_id={order_id}")

    try:
        with Session(engine) as session:
            order = session.exec(
                select(Order).where(Order.order_id == order_id)
            ).first()

            if not order:
                logger.error(f"[Celery] Order not found: {order_id}")
                return False

            if not can_transition(order.status, OrderStatus.VALIDATED):
                logger.error(
                    f"[Celery] Invalid state transition for order {order_id}: "
                    f"{order.status.value} -> {OrderStatus.VALIDATED.value}"
                )
                return False

            _append_order_log(order, "order_validating_started", "Order validating started by celery worker")
            session.add(order)
            session.commit()
            logger.info(f"[Celery] Order {order_id} validating started")

            # Fetch addresses from OMS (with retry on 503/timeout).
            # OMS identifies orders by orderNo = the platform source order id;
            # our internal order_id (UUID) is unknown to OMS.
            # Parallel-card orders ship to the base card's address: reuse the
            # base order's stored addresses when present, otherwise ask OMS
            # with the base prefix. Either way the rows are re-keyed onto this
            # order so the push payload lookup finds them.
            try:
                if order.type == OrderType.PARALLEL_CARD:
                    addresses = _resolve_parallel_card_addresses(session, order)
                else:
                    fetched = oms_service.fetch_order_addresses(order.source_order_id, session)
                    addresses = _attach_addresses_to_order(session, order, fetched)
            except OMSRetryableError as oms_exc:
                retry_count = order_data.get("_oms_retry_count", 0)
                max_retries = settings.OMS_VALIDATE_RETRY_COUNT
                base_delay = settings.OMS_VALIDATE_RETRY_COUNTDOWN
                max_delay = settings.OMS_VALIDATE_RETRY_MAX_COUNTDOWN
                if retry_count < max_retries:
                    countdown = _exponential_backoff(base_delay, retry_count, max_delay)
                    order_data["_oms_retry_count"] = retry_count + 1
                    logger.warning(
                        f"[Celery] OMS retryable error for order {order_id} "
                        f"(attempt {retry_count + 1}/{max_retries}), retrying in {countdown}s: {oms_exc}"
                    )
                    _append_order_log(
                        order,
                        "order_oms_retry",
                        f"OMS retryable error (attempt {retry_count + 1}/{max_retries}), retrying in {countdown}s: {oms_exc}",
                    )
                    session.add(order)
                    session.commit()
                    validate_order.apply_async(args=[order_data], countdown=countdown)
                    return True
                else:
                    logger.error(
                        f"[Celery] OMS retryable error for order {order_id} "
                        f"after {max_retries} attempts, marking as FAILED: {oms_exc}"
                    )
                    _mark_order_failed(order_id, f"OMS API failed after {max_retries} retries: {oms_exc}")
                    return False

            logger.info(f"[Celery] OMS addresses for order {order_id}: {addresses}")

            if not addresses.get("delivery"):
                _mark_order_failed(order_id,  "No delivery address returned by OMS")
                return False

            order.status = OrderStatus.VALIDATED
            session.add(order)
            session.commit()

            # Notify OMS/VFS of the dataready status ourselves — QPMN never
            # sends an order_item_* event for VALIDATED (confirmed with QPMN),
            # so receive_order_status() never gets a chance to raise this one.
            from app.tasks.notifications import notify_oms, notify_vfs  # lazy import, avoids circular dependency

            dataready_status = OMS_STATUS_MAP[OrderStatus.VALIDATED]

            oms_outbound_log = WebhookLog(
                direction=WebhookDirection.OUTBOUND,
                source="OMS",
                order_id=order.order_id,
                source_order_id=order.source_order_id,
                store_order_id=order.store_order_id,
                event_status=OrderStatus.VALIDATED.value,
                payload={
                    "orderNo": order.order_id,
                    "status": dataready_status,
                    "shipments": [],
                },
                process_status=WebhookProcessStatus.RECEIVED,
            )
            session.add(oms_outbound_log)
            session.commit()
            session.refresh(oms_outbound_log)

            notify_oms.delay(
                webhook_log_id=oms_outbound_log.id,
                order_id=order.order_id,
                event_status=dataready_status,
                shipments=None,
            )

            vfs_outbound_log = WebhookLog(
                direction=WebhookDirection.OUTBOUND,
                source="VFS",
                order_id=order.order_id,
                source_order_id=order.source_order_id,
                store_order_id=order.store_order_id,
                event_status=OrderStatus.VALIDATED.value,
                payload={
                    "sourceOrderId": order.source_order_id,
                    "status": dataready_status,
                    "shipments": [],
                },
                process_status=WebhookProcessStatus.RECEIVED,
            )
            session.add(vfs_outbound_log)
            session.commit()
            session.refresh(vfs_outbound_log)

            notify_vfs.delay(
                webhook_log_id=vfs_outbound_log.id,
                order_id=order.order_id,
                event_status=dataready_status,
                shipments=None,
            )

            # Chain to push_order
            task_payload = {
                "order_id": order.order_id,
                "source_order_id": order.source_order_id,
                "status": order.status.value,
                "created_at": order.created_at.isoformat() if order.created_at else None,
            }
            push_order.apply_async(args=[task_payload])
            return True

    except Exception as e:
        logger.error(f"[Celery] Failed to validate order {order_id}: {e}", exc_info=True)
        _mark_order_failed(order_id, str(e))
        return False


@celery_app.task(bind=True, name="tasks.orders.push_order", queue=QUEUE_ORDER_PUSHING)
def push_order(self, order_data: Dict[str, Any]) -> bool:
    """
    Push a processing order to the QPMN platform.

    Args:
        order_data: Order data dictionary.

    Returns:
        True if successful, False otherwise.
    """
    order_id = order_data.get("order_id")
    logger.info(f"[Celery] Pushing order task: order_id={order_id}")
    try:
        with Session(engine) as session:
            order = session.exec(
                select(Order).where(Order.order_id == order_id)
            ).first()

            if not order:
                logger.error(f"[Celery] Order not found: {order_id}")
                return False

            if not can_transition(order.status, OrderStatus.PROCESSING):
                logger.error(
                    f"[Celery] Invalid state transition for order {order_id}: "
                    f"{order.status.value} -> {OrderStatus.PROCESSING.value}"
                )
                return False

            _append_order_log(order, "order_pushing_started", "Order pushing started by celery worker")

            logger.info(f"[Celery] Order {order_id} pushing to QPMN (current status: {order.status.value})")

            # Build payload via order_service (lazy import to avoid circular dependency)
            from app.services.order import order_service

            # Parallel-card orders: use the parent (base card) order's QPMN
            # store_order_id as the barcode, persisted once before building
            # the payload — retries (503 backoff) reuse the same value, and
            # the payload builders echo it into each item's supplierStockNo
            # with the item's 1-based position as a two-digit suffix (01, 02,
            # ...). Without a parent store_order_id (base not pushed yet)
            # barcode stays empty and supplierStockNo is omitted.
            if order.type == OrderType.PARALLEL_CARD and not order.barcode:
                parent = order_service.find_parallel_card_parent(
                    session, order.source_order_id, store_id=order.store_id
                )
                if parent and parent.store_order_id:
                    order.barcode = str(parent.store_order_id)
                    _append_order_log(
                        order, "barcode_generated",
                        f"Barcode {order.barcode} taken from parent order "
                        f"{parent.source_order_id} store_order_id",
                    )
                    session.add(order)
                    session.commit()
                else:
                    logger.warning(
                        f"[Celery] Order {order_id}: parallel card has no parent "
                        f"store_order_id, skipping barcode (supplierStockNo omitted)"
                    )

            payload = order_service.build_push_payload(session, order_id)

            # Persist the payload actually submitted to QPMN (overwritten on
            # each retry) for auditing and replay of the last push attempt.
            order.creation_payload = payload
            session.add(order)
            session.commit()

            # Select API URL based on configured API version
            use_open_api = settings.QPMN_ORDER_API_VERSION == "open"
            if use_open_api:
                api_url = f"{settings.QPMN_OPEN_API_URL.rstrip('/')}/orders"
            else:
                api_url = f"{settings.QPMN_API_URL}/store/orders"

            store_key = client_service.get_store_key_by_id(order.store_id)
            headers = {"Authorization": f"Basic {store_key}"}
            max_retries = settings.QPMN_PUSH_RETRY_COUNT
            base_delay = settings.QPMN_PUSH_RETRY_COUNTDOWN
            max_delay = settings.QPMN_PUSH_RETRY_MAX_COUNTDOWN
            retry_count = order_data.get("_qpmn_retry_count", 0)
            try:
                with httpx.Client(timeout=30) as client:
                    response = client.post(api_url, json=payload, headers=headers)
                # Handle 503/504 (service unavailable / gateway timeout) -
                # both are transient server-side failures, retry with backoff
                if response.status_code in (503, 504):
                    retry_alert = "RETRY_503" if response.status_code == 503 else "RETRY_504"
                    exhausted_alert = "RETRY_EXHAUSTED_503" if response.status_code == 503 else "RETRY_EXHAUSTED_504"
                    if retry_count < 0 :
                        countdown = _exponential_backoff(base_delay, retry_count, max_delay)
                        order_data["_qpmn_retry_count"] = retry_count + 1
                        logger.warning(
                            f"[Celery] QPMN returned {response.status_code} for order {order_id} "
                            f"(attempt {retry_count + 1}/{max_retries}), retrying in {countdown}s"
                        )
                        if retry_count == 0:
                            _capture_push_alert(retry_alert, order_id)
                        _append_order_log(
                            order, "order_push_retry",
                            f"QPMN returned {response.status_code} (attempt {retry_count + 1}/{max_retries}), retrying in {countdown}s",
                        )
                        session.add(order)
                        session.commit()
                        push_order.apply_async(args=[order_data], countdown=countdown)
                        return True
                    else:
                        logger.error(
                            f"[Celery] QPMN returned {response.status_code} for order {order_id} "
                            f"after {max_retries} attempts, marking as FAILED"
                        )
                        _capture_push_alert(exhausted_alert, order_id, max_retries=max_retries)
                        _mark_order_failed(order_id, f"QPMN returned {response.status_code} after {max_retries} retries")
                        return False

                result = response.json()
                success = result.get("success", False)

                # Debug: dump the full request payload and response to the log
                # (visible when DEBUG=true — the module logger is raised to
                # DEBUG in that case, overriding the worker's INFO default)
                logger.debug(
                    f"[Celery] Order {order_id} QPMN push debug\n"
                    f"URL: {api_url}\n"
                    f"payload: {json.dumps(payload, ensure_ascii=False, default=str)}\n"
                    f"response: {json.dumps(result, ensure_ascii=False, default=str)}"
                )

                if success:
                    # Order pushed successfully - mark as processing
                    order.status = OrderStatus.PROCESSING
                    # Legacy API returns orderId in data; Open API returns id in data
                    data = result.get("data", {})
                    store_order_id = data.get("id") or data.get("externalId") or data.get("orderId")
                    if not store_order_id and not isinstance(data, dict):
                        store_order_id = result.get("orderId")
                    order.store_order_id = store_order_id if store_order_id else None
                    # TI-65: record the expected set of QPMN item ids so we
                    # can later tell when every one of them has been produced.
                    item_ids = _extract_store_order_item_ids(data)
                    order.store_order_item_ids = item_ids or None
                    _append_order_log(order, "order_push_success", "Order pushed to QPMN successfully")
                    session.add(order)
                    session.commit()
                    logger.info(f"[Celery] Order {order_id} pushed to QPMN successfully, status -> PROCESSING")
                else:
                    # Order push failed - mark as FAILED and log message (no retry, so this is final)
                    error_message = result.get("data", {}).get("message", "Unknown error")
                    if not error_message and isinstance(result.get("data"), dict):
                        error_message = result["data"].get("error", "Unknown error")
                    order.status = OrderStatus.FAILED
                    _append_order_log(order, "order_push_failed", f"QPMN returned success=false: {error_message}")
                    session.add(order)
                    session.commit()
                    _capture_push_alert("REJECTED", order_id, error_message=error_message)
                    logger.error(f"[Celery] Order {order_id} push failed: {result}")
                    logger.info(f"[Celery] Order {order_id} payload: {payload}")

            except httpx.TimeoutException:
                if retry_count < max_retries:
                    countdown = _exponential_backoff(base_delay, retry_count, max_delay)
                    order_data["_qpmn_retry_count"] = retry_count + 1
                    logger.warning(
                        f"[Celery] QPMN timeout for order {order_id} "
                        f"(attempt {retry_count + 1}/{max_retries}), retrying in {countdown}s"
                    )
                    if retry_count == 0:
                        _capture_push_alert("RETRY_TIMEOUT", order_id)
                    _append_order_log(
                        order, "order_push_timeout",
                        f"QPMN request timeout (attempt {retry_count + 1}/{max_retries}), retrying in {countdown}s",
                    )
                    session.add(order)
                    session.commit()
                    push_order.apply_async(args=[order_data], countdown=countdown)
                    return True
                else:
                    logger.error(
                        f"[Celery] QPMN timeout for order {order_id} "
                        f"after {max_retries} attempts, marking as FAILED"
                    )
                    _capture_push_alert("RETRY_EXHAUSTED_TIMEOUT", order_id, max_retries=max_retries)
                    _mark_order_failed(order_id, f"QPMN timeout after {max_retries} retries")
                    return False

            return True

    except Exception as e:
        logger.error(f"[Celery] Failed to publish order {order_id}: {e}", exc_info=True)
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("order_queue", QUEUE_ORDER_PUSHING)
            scope.set_tag("failure_type", "unexpected_exception")
            scope.set_tag("order_id", order_id)
            sentry_sdk.capture_exception(e)
        _mark_order_failed(order_id, str(e))
        return False


def _append_order_log(order: Order, action: str, message: str, worker: str = "celery_order_worker") -> None:
    """Append a log entry to an order and flag the field as modified."""
    if order.logs is None:
        order.logs = []
    order.logs.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "message": message,
        "worker": worker,
    })
    attributes.flag_modified(order, "logs")


def _mark_order_failed(order_id: str, error_message: str):
    """Helper to mark an order as failed."""
    try:
        with Session(engine) as session:
            order = session.exec(
                select(Order).where(Order.order_id == order_id)
            ).first()
            if order:
                order.status = OrderStatus.FAILED
                _append_order_log(order, "order_processing_failed", f"Processing failed: {error_message}")
                session.add(order)
                session.commit()
    except Exception as log_error:
        logger.error(f"[Celery] Failed to update order status to FAILED: {log_error}")
