"""Order processing Celery tasks."""
import logging
import tempfile
from typing import Dict, Any, List
from datetime import datetime, timezone
from sqlmodel import Session, select
from app.core.celery import celery_app
from app.core.database import engine
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

            if order.logs is None:
                order.logs = []
            order.logs.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "order_publishing_started",
                "message": "Order publishing started by celery worker",
                "worker": "celery_order_worker",
            })

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
                                order.logs.append({
                                    "timestamp": datetime.now(timezone.utc).isoformat(),
                                    "action": "download_order_files",
                                    "message": result,
                                    "worker": "celery_order_worker",
                                })
                                order.status = OrderStatus.FAILED
                                session.add(order)
                                session.commit()
                                return False

                            if result.lower().endswith(".pdf"):
                                page_files = file_service.split_pdf(result, tmp_dir)
                            else:
                                page_files = [result]

                            for page_file in page_files:
                                upload_result = file_service.upload_to_qpmn(page_file, store_key)
                                if upload_result:
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

            if order.logs is None:
                order.logs = []
            order.logs.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "action": "order_validating_started",
                "message": "Order validating started by celery worker",
                "worker": "celery_order_worker",
            })
            session.add(order)
            session.commit()
            logger.info(f"[Celery] Order {order_id} validating started")

            # Fetch addresses from OMS
            addresses = oms_service.fetch_order_addresses(order_id, session)
            logger.info(f"[Celery] OMS addresses for order {order_id}: {addresses}")

            if not addresses.get("delivery"):
                order.status = OrderStatus.FAILED
                order.logs.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "action": "order_validation_failed",
                    "message": "No delivery address returned by OMS",
                    "worker": "celery_order_worker",
                })
                session.add(order)
                session.commit()
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
    return False


def _mark_order_failed(order_id: str, error_message: str):
    """Helper to mark an order as failed."""
    try:
        with Session(engine) as session:
            order = session.exec(
                select(Order).where(Order.order_id == order_id)
            ).first()
            if order:
                order.status = OrderStatus.FAILED
                if order.logs is None:
                    order.logs = []
                order.logs.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "action": "order_processing_failed",
                    "message": f"Processing failed: {error_message}",
                    "worker": "celery_order_worker",
                })
                session.add(order)
                session.commit()
    except Exception as log_error:
        logger.error(f"[Celery] Failed to update order status to FAILED: {log_error}")
