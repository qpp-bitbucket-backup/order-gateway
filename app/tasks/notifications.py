"""Outbound notification Celery tasks."""
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from sqlmodel import Session, select

from app.core.celery import celery_app
from app.core.database import engine
from app.core.rabbitmq import QUEUE_ORDER_NOTIFYING
from app.models.order import Order
from app.models.webhook_log import WebhookLog, WebhookProcessStatus
from app.services.oms import oms_service
from app.services.vfs import vfs_service

logger = logging.getLogger(__name__)


@celery_app.task(
    bind=True,
    name="tasks.notifications.notify_oms",
    queue=QUEUE_ORDER_NOTIFYING,
)
def notify_oms(
    self,
    webhook_log_id: int,
    order_id: str,
    event_status: str,
    shipments: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """
    Notify OMS of an order status update via API-002 (HUB4 transport).

    Success → mark the outbound log ``processed``.
    4xx / business error → mark ``failed`` (no retry).
    5xx / network error → update ``retry_count`` + ``details`` then
    re-enqueue itself 15 minutes out (same pattern as ``push_order`` for
    QPMN) — unlike ``notify_vfs``, there's no retry cap; it keeps retrying
    every 15 minutes until OMS accepts it.
    """
    logger.info(
        "[Celery] notify_oms: log_id=%s order_id=%s status=%s",
        webhook_log_id,
        order_id,
        event_status,
    )

    try:
        with Session(engine) as session:
            log = session.exec(
                select(WebhookLog).where(WebhookLog.id == webhook_log_id)
            ).first()
            if not log:
                logger.error("[Celery] WebhookLog not found: %s", webhook_log_id)
                return False

            order = session.exec(
                select(Order).where(Order.order_id == order_id)
            ).first()
            if not order:
                logger.error("[Celery] Order not found: %s", order_id)
                log.process_status = WebhookProcessStatus.FAILED
                log.details = "Order not found"
                log.updated_at = datetime.now(timezone.utc)
                session.add(log)
                session.commit()
                return False

            result = oms_service.update_order_status(
                order=order,
                event_status=event_status,
                status_desc=event_status,
                shipments=shipments,
            )

            if result.get("request_payload") is not None:
                log.payload = result.get("request_payload")

            if result.get("success"):
                log.process_status = WebhookProcessStatus.PROCESSED
                log.details = result.get("response")
                log.updated_at = datetime.now(timezone.utc)
                session.add(log)
                session.commit()
                logger.info("[Celery] OMS notified for order %s", order_id)
                return True

            # Non-retryable failure (4xx, business error, not configured)
            log.process_status = WebhookProcessStatus.FAILED
            log.details = result.get("message", "OMS returned failure")
            log.retry_count = self.request.retries
            log.updated_at = datetime.now(timezone.utc)
            session.add(log)
            session.commit()
            logger.warning("[Celery] OMS business error for order %s: %s", order_id, result)
            return False

    except Exception as e:
        logger.error(
            "[Celery] notify_oms failed for log %s: %s",
            webhook_log_id,
            e,
            exc_info=True,
        )
        try:
            with Session(engine) as session:
                log = session.exec(
                    select(WebhookLog).where(WebhookLog.id == webhook_log_id)
                ).first()
                if log:
                    log.retry_count = (log.retry_count or 0) + 1
                    log.details = str(e)[:512]
                    log.updated_at = datetime.now(timezone.utc)
                    session.add(log)
                    session.commit()
        except Exception as log_err:
            logger.error("[Celery] Failed to update webhook log: %s", log_err)

        logger.warning(
            "[Celery] notify_oms failed for order %s, retrying in 15 minutes", order_id
        )
        notify_oms.apply_async(
            args=[webhook_log_id, order_id, event_status, shipments],
            countdown=900,
        )
        return True


# Site Flow's official trigger retry curve: 6 min, 15 min, 30 min, then a
# final attempt 24 h after the initial failure. VFS-specific — other
# notification tasks keep their own retry policy.
VFS_RETRY_COUNTDOWNS = [6 * 60, 15 * 60, 30 * 60, 24 * 60 * 60]


@celery_app.task(
    bind=True,
    name="tasks.notifications.notify_vfs",
    queue=QUEUE_ORDER_NOTIFYING,
    max_retries=len(VFS_RETRY_COUNTDOWNS),
)
def notify_vfs(
    self,
    webhook_log_id: int,
    order_id: str,
    event_status: str,
    shipments: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """
    Notify VFS of an order status update via the postback webhook.

    Success → mark the outbound log ``processed``.
    4xx / business error → mark ``failed`` (no retry).
    5xx / network error → retry on Site Flow's official trigger curve
    (6 min → 15 min → 30 min → 24 h, see ``VFS_RETRY_COUNTDOWNS``); once
    the curve is exhausted the log is left as ``failed``.
    """
    logger.info(
        "[Celery] notify_vfs: log_id=%s order_id=%s status=%s",
        webhook_log_id,
        order_id,
        event_status,
    )

    try:
        with Session(engine) as session:
            log = session.exec(
                select(WebhookLog).where(WebhookLog.id == webhook_log_id)
            ).first()
            if not log:
                logger.error("[Celery] WebhookLog not found: %s", webhook_log_id)
                return False

            order = session.exec(
                select(Order).where(Order.order_id == order_id)
            ).first()
            if not order:
                logger.error("[Celery] Order not found: %s", order_id)
                log.process_status = WebhookProcessStatus.FAILED
                log.details = "Order not found"
                log.updated_at = datetime.now(timezone.utc)
                session.add(log)
                session.commit()
                return False

            result = vfs_service.send_status_postback(
                order=order,
                event_status=event_status,
                shipments=shipments,
            )

            if result.get("request_payload") is not None:
                log.payload = result.get("request_payload")

            if result.get("success"):
                log.process_status = WebhookProcessStatus.PROCESSED
                log.details = result.get("response")
                log.updated_at = datetime.now(timezone.utc)
                session.add(log)
                session.commit()
                logger.info("[Celery] VFS notified for order %s", order_id)
                return True

            # Non-retryable failure (4xx, business error, not configured)
            log.process_status = WebhookProcessStatus.FAILED
            log.details = result.get("message", "VFS postback failed")
            log.retry_count = self.request.retries
            log.updated_at = datetime.now(timezone.utc)
            session.add(log)
            session.commit()
            logger.warning("[Celery] VFS postback error for order %s: %s", order_id, result)
            return False

    except Exception as e:
        logger.error(
            "[Celery] notify_vfs failed for log %s: %s",
            webhook_log_id,
            e,
            exc_info=True,
        )
        try:
            with Session(engine) as session:
                log = session.exec(
                    select(WebhookLog).where(WebhookLog.id == webhook_log_id)
                ).first()
                if log:
                    log.retry_count = self.request.retries
                    log.details = str(e)[:512]
                    if self.request.retries >= self.max_retries:
                        log.process_status = WebhookProcessStatus.FAILED
                    log.updated_at = datetime.now(timezone.utc)
                    session.add(log)
                    session.commit()
        except Exception as log_err:
            logger.error("[Celery] Failed to update webhook log: %s", log_err)

        # Retry on the Site Flow curve; raise the original error once exhausted.
        if self.request.retries < len(VFS_RETRY_COUNTDOWNS):
            raise self.retry(exc=e, countdown=VFS_RETRY_COUNTDOWNS[self.request.retries])
        raise
