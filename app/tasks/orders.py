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
from app.services.oms import oms_service

logger = logging.getLogger(__name__)


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

            uploaded_files: List[Dict[str, Any]] = []
            products = order.order_data.get("items", [])
            store_id = order.store_id
            store_key = client_service.get_store_key_by_id(store_id)
            
            with tempfile.TemporaryDirectory(prefix=f"order_{order_id}_") as tmp_dir:
                for product in products:
                    components = product.get("components", [])
                    for component in components:
                        file_url = component.get("path", None)
                        if not file_url:
                            continue

                        logger.info(f"[Celery] Processing file: {file_url}")

                        try:
                            success, result = file_service.download_file(file_url, tmp_dir)
                            if not success:
                                _mark_order_failed(order_id,"Cannot download design files")
                                logger.error(f"File: [{file_url}] download failed") 
                                return False

                            if result.lower().endswith(".pdf"):
                                page_files = file_service.split_pdf(result, tmp_dir)
                                if not page_files:
                                    _mark_order_failed(order_id,"Cannot split PDF file")
                                    logger.error(f"File: [{file_url}] PDF split failed") 
                                    return False
                            else:
                                page_files = [result]

                            for page_file in page_files:
                                upload_result = file_service.upload_to_qpmn(page_file, store_key)
                                if not upload_result:
                                    _mark_order_failed(order_id,"Cannot upload design files")
                                    raise ValueError(f"File: [{page_file}] upload failed")
                                    return False
                                uploaded_files.append(upload_result)

                        except Exception as file_err:
                            logger.error(
                                f"[Celery] Failed to process file {file_url}: {file_err}",
                                exc_info=True,
                            )

                if uploaded_files:
                    order.files = uploaded_files
                    session.add(order)
                    session.commit()
                    logger.info(
                        f"[Celery] Saved {len(uploaded_files)} file(s) to order {order_id}"
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

            # Fetch addresses from OMS
            addresses = oms_service.fetch_order_addresses(order_id, session)
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
    Push a validated order to the QPMN platform.

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

            if not can_transition(order.status, OrderStatus.PRINTREADY):
                logger.error(
                    f"[Celery] Invalid state transition for order {order_id}: "
                    f"{order.status.value} -> {OrderStatus.PRINTREADY.value}"
                )
                return False

            _append_order_log(order, "order_pushing_started", "Order pushing started by celery worker")

            logger.info(f"[Celery] Order {order_id} status updated to PENDING")

            # Build payload via order_service (lazy import to avoid circular dependency)
            from app.services.order import order_service
            payload = order_service.merge_order(session, order_id)

            # POST to QPMN /store/orders API
            store_key = client_service.get_store_key_by_id(order.store_id)
            api_url = f"{settings.QPMN_API_URL}/store/orders"
            headers = {"Authorization": f"Basic {store_key}"}

            try:
                with httpx.Client(timeout=30.0) as client:
                    response = client.post(api_url, json=payload, headers=headers)

                # Handle timeout or 503 - retry with 15 min delay
                if response.status_code == 503:
                    logger.warning(f"[Celery] QPMN returned 503 for order {order_id}, retrying in 15 minutes")
                    _append_order_log(order, "order_push_retry", "QPMN returned 503, retrying in 15 minutes")
                    session.add(order)
                    session.commit()
                    push_order.apply_async(args=[order_data], countdown=900)  # 15 minutes = 900 seconds
                    return True

                response.raise_for_status()
                result = response.json()
                success = result.get("success", False)

                if success:
                    # Order pushed successfully - mark as received
                    order.status = OrderStatus.PRINTREADY
                    _append_order_log(order, "order_push_success", "Order pushed to QPMN successfully")
                    session.add(order)
                    session.commit()
                    logger.info(f"[Celery] Order {order_id} pushed to QPMN successfully")
                else:
                    # Order push failed - mark as FAILED and log message
                    error_message = result.get("data", {}).get("message", "Unknown error")
                    order.status = OrderStatus.FAILED
                    _append_order_log(order, "order_push_failed", f"QPMN returned success=false: {error_message}")
                    session.add(order)
                    session.commit()
                    logger.error(f"[Celery] Order {order_id} push failed: {result}")

            except httpx.TimeoutException:
                logger.warning(f"[Celery] QPMN timeout for order {order_id}, retrying in 15 minutes")
                _append_order_log(order, "order_push_timeout", "QPMN request timeout, retrying in 15 minutes")
                session.add(order)
                session.commit()
                push_order.apply_async(args=[order_data], countdown=900)  # 15 minutes
                return True

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
