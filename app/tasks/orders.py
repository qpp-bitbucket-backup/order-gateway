"""Order processing Celery tasks."""
import logging
import tempfile
import httpx
from typing import Dict, Any, List
from datetime import datetime, timezone
from sqlmodel import Session, select
from sqlalchemy.orm import attributes
from app.core.celery import celery_app
from app.core.database import engine
from app.core.config import settings
from app.core.rabbitmq import QUEUE_ORDER_PUBLISHING, QUEUE_ORDER_VALIDATING, QUEUE_ORDER_PUSHING
from app.models.order import Order, OrderStatus, can_transition
from app.services.file import file_service
from app.services.client import client_service
from app.services.oms import oms_service, OMSRetryableError

logger = logging.getLogger(__name__)


def _exponential_backoff(base: int, retry_count: int, cap: int) -> int:
    """Calculate exponential backoff delay: min(base * 2^retry_count, cap)."""
    return min(base * (2 ** retry_count), cap)


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
            file_quantity = 0
            file_index = 0
            with tempfile.TemporaryDirectory(prefix=f"order_{order_id}_") as tmp_dir:
                for product in products:
                    components = product.get("components", [])
                    sku = product.get("sku", None)
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

                            for page_file in page_files:
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

            # Fetch addresses from OMS (with retry on 503/timeout)
            try:
                addresses = oms_service.fetch_order_addresses(order_id, session)
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
            payload = order_service.build_push_payload(session, order_id)

            # Select API URL based on configured API version
            use_open_api = settings.QPMN_ORDER_API_VERSION == "open"
            if use_open_api:
                api_url = f"{settings.QPMN_OPEN_API_URL}/store/orders"
            else:
                api_url = f"{settings.QPMN_API_URL}/store/orders"
            store_key = client_service.get_store_key_by_id(order.store_id)
            headers = {"Authorization": f"Basic {store_key}"}
            max_retries = settings.QPMN_PUSH_RETRY_COUNT
            base_delay = settings.QPMN_PUSH_RETRY_COUNTDOWN
            max_delay = settings.QPMN_PUSH_RETRY_MAX_COUNTDOWN
            retry_count = order_data.get("_qpmn_retry_count", 0)

            try:
                with httpx.Client(timeout=30.0) as client:
                    response = client.post(api_url, json=payload, headers=headers)

                # Handle 503 - retry with delay
                if response.status_code == 503:
                    if retry_count < max_retries:
                        countdown = _exponential_backoff(base_delay, retry_count, max_delay)
                        order_data["_qpmn_retry_count"] = retry_count + 1
                        logger.warning(
                            f"[Celery] QPMN returned 503 for order {order_id} "
                            f"(attempt {retry_count + 1}/{max_retries}), retrying in {countdown}s"
                        )
                        _append_order_log(
                            order, "order_push_retry",
                            f"QPMN returned 503 (attempt {retry_count + 1}/{max_retries}), retrying in {countdown}s",
                        )
                        session.add(order)
                        session.commit()
                        push_order.apply_async(args=[order_data], countdown=countdown)
                        return True
                    else:
                        logger.error(
                            f"[Celery] QPMN returned 503 for order {order_id} "
                            f"after {max_retries} attempts, marking as FAILED"
                        )
                        _mark_order_failed(order_id, f"QPMN returned 503 after {max_retries} retries")
                        return False

                result = response.json()
                success = result.get("success", False)

                if success:
                    # Order pushed successfully - mark as processing
                    order.status = OrderStatus.PROCESSING
                    # Legacy API returns orderId in data; Open API returns id in data
                    data = result.get("data", {})
                    store_order_id = data.get("orderId") or data.get("id")
                    if not store_order_id and not isinstance(data, dict):
                        store_order_id = result.get("orderId")
                    order.store_order_id = str(store_order_id) if store_order_id else None
                    _append_order_log(order, "order_push_success", "Order pushed to QPMN successfully")
                    session.add(order)
                    session.commit()
                    logger.info(f"[Celery] Order {order_id} pushed to QPMN successfully, status -> PROCESSING")
                else:
                    # Order push failed - mark as FAILED and log message
                    error_message = result.get("data", {}).get("message", "Unknown error")
                    if not error_message and isinstance(result.get("data"), dict):
                        error_message = result["data"].get("error", "Unknown error")
                    order.status = OrderStatus.FAILED
                    _append_order_log(order, "order_push_failed", f"QPMN returned success=false: {error_message}")
                    session.add(order)
                    session.commit()
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
                    _mark_order_failed(order_id, f"QPMN timeout after {max_retries} retries")
                    return False

            return True

    except Exception as e:
        logger.error(f"[Celery] Failed to publish order {order_id}: {e}", exc_info=True)
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
