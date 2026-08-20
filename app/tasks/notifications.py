"""Outbound notification Celery tasks."""
import json
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

import sentry_sdk
from sqlmodel import Session, select

from app.core.celery import celery_app
from app.core.config import settings
from app.core.database import engine
from app.core.rabbitmq import QUEUE_ORDER_NOTIFYING
from app.models.order import Order
from app.models.webhook_log import WebhookLog, WebhookProcessStatus
from app.services.oms import oms_service
from app.services.vfs import vfs_service
from app.tasks.orders import _exponential_backoff

logger = logging.getLogger(__name__)


def _capture_notify_alert(level: str, message: str, order_id: Optional[str], channel: str, failure_type: str) -> None:
    """Capture a notify_oms/notify_vfs failure to Sentry.

    Same tagging/fingerprint approach as _capture_push_alert in
    app.tasks.orders — ``channel`` ("oms"/"vfs") plus ``failure_type`` lets
    alert rules target either postback channel independently.

    fingerprint is pinned to (channel, failure_type) so failures aggregate
    into one issue per channel/failure_type instead of one per order_id.
    """
    with sentry_sdk.new_scope() as scope:
        scope.set_tag("order_queue", QUEUE_ORDER_NOTIFYING)
        scope.set_tag("channel", channel)
        scope.set_tag("failure_type", failure_type)
        if order_id:
            scope.set_tag("order_id", order_id)
        scope.fingerprint = [channel, failure_type]
        sentry_sdk.capture_message(message, level=level)


@celery_app.task(
    name="tasks.notifications.notify_oms",
    queue=QUEUE_ORDER_NOTIFYING,
)
def notify_oms(
    webhook_log_id: int,
    order_id: str,
    event_status: str,
    shipments: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """
    Notify OMS of an order status update via API-002 (HUB4 transport).

    Success → mark the outbound log ``processed``.
    4xx / business error → mark ``failed`` (no retry).
    5xx / network error → exponential backoff retry, up to
    ``OMS_NOTIFY_RETRY_COUNT`` attempts; once exhausted the ``WebhookLog``
    is left ``failed``. Whether the *order* itself should also transition
    to FAILED at that point is unconfirmed with Ivan — not wired up here,
    see docs/order-gateway-oms-todo.md.
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
                _capture_notify_alert(
                    "warning",
                    f"notify_oms: order {order_id} not found for webhook_log {webhook_log_id}",
                    order_id,
                    "oms",
                    "oms_order_not_found",
                )
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
                log.details = json.dumps(result.get("response")) if result.get("response") is not None else None
                log.updated_at = datetime.now(timezone.utc)
                session.add(log)
                session.commit()
                logger.info("[Celery] OMS notified for order %s", order_id)
                return True

            # Non-retryable failure (4xx, business error, not configured)
            log.process_status = WebhookProcessStatus.FAILED
            log.details = json.dumps({
                "errorCode": result.get("error_code"),
                "errorMsg": result.get("message", "OMS returned failure"),
            })
            log.updated_at = datetime.now(timezone.utc)
            session.add(log)
            session.commit()
            logger.warning("[Celery] OMS business error for order %s: %s", order_id, result)
            _capture_notify_alert(
                "error",
                f"notify_oms: OMS rejected order {order_id}: {result.get('message', 'OMS returned failure')}",
                order_id,
                "oms",
                "oms_rejected",
            )
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
                if not log:
                    return True

                retry_count = log.retry_count or 0
                max_retries = settings.OMS_NOTIFY_RETRY_COUNT
                base_delay = settings.OMS_NOTIFY_RETRY_COUNTDOWN
                max_delay = settings.OMS_NOTIFY_RETRY_MAX_COUNTDOWN

                if retry_count < max_retries:
                    countdown = _exponential_backoff(base_delay, retry_count, max_delay)
                    log.retry_count = retry_count + 1
                    log.details = str(e)[:512]
                    log.updated_at = datetime.now(timezone.utc)
                    session.add(log)
                    session.commit()
                    logger.warning(
                        "[Celery] notify_oms failed for order %s (attempt %s/%s), retrying in %ss",
                        order_id, retry_count + 1, max_retries, countdown,
                    )
                    if retry_count == 0:
                        _capture_notify_alert(
                            "warning",
                            f"notify_oms: failed for order {order_id}, first retry scheduled: {e}",
                            order_id,
                            "oms",
                            "oms_retry",
                        )
                    notify_oms.apply_async(
                        args=[webhook_log_id, order_id, event_status, shipments],
                        countdown=countdown,
                    )
                    return True

                log.process_status = WebhookProcessStatus.FAILED
                log.details = str(e)[:512]
                log.updated_at = datetime.now(timezone.utc)
                session.add(log)
                session.commit()
                logger.error(
                    "[Celery] notify_oms exhausted %s retries for order %s", max_retries, order_id,
                )
                _capture_notify_alert(
                    "error",
                    f"notify_oms: exhausted {max_retries} retries for order {order_id}: {e}",
                    order_id,
                    "oms",
                    "oms_retry_exhausted",
                )
                return False
        except Exception as log_err:
            logger.error("[Celery] Failed to update webhook log: %s", log_err)
            return False


@celery_app.task(
    name="tasks.notifications.notify_vfs",
    queue=QUEUE_ORDER_NOTIFYING,
)
def notify_vfs(
    webhook_log_id: int,
    order_id: str,
    event_status: str,
    shipments: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """
    Notify VFS of an order status update via the postback webhook.

    Success → mark the outbound log ``processed``.
    4xx / business error → mark ``failed`` (no retry).
    5xx / network error → exponential backoff retry, up to
    ``VFS_NOTIFY_RETRY_COUNT`` attempts; once exhausted the ``WebhookLog``
    is left ``failed``. Whether the *order* itself should also transition
    to FAILED at that point is unconfirmed with Ivan — not wired up here,
    see docs/order-gateway-oms-todo.md.
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
                log.details = json.dumps(result.get("response")) if result.get("response") is not None else None
                log.updated_at = datetime.now(timezone.utc)
                session.add(log)
                session.commit()
                logger.info("[Celery] VFS notified for order %s", order_id)
                return True

            # Non-retryable failure (4xx, business error, not configured)
            log.process_status = WebhookProcessStatus.FAILED
            log.details = json.dumps({
                "errorCode": result.get("error_code"),
                "errorMsg": result.get("message", "VFS postback failed"),
            })
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
                if not log:
                    return True

                retry_count = log.retry_count or 0
                max_retries = settings.VFS_NOTIFY_RETRY_COUNT
                base_delay = settings.VFS_NOTIFY_RETRY_COUNTDOWN
                max_delay = settings.VFS_NOTIFY_RETRY_MAX_COUNTDOWN

                if retry_count < max_retries:
                    countdown = _exponential_backoff(base_delay, retry_count, max_delay)
                    log.retry_count = retry_count + 1
                    log.details = str(e)[:512]
                    log.updated_at = datetime.now(timezone.utc)
                    session.add(log)
                    session.commit()
                    logger.warning(
                        "[Celery] notify_vfs failed for order %s (attempt %s/%s), retrying in %ss",
                        order_id, retry_count + 1, max_retries, countdown,
                    )
                    notify_vfs.apply_async(
                        args=[webhook_log_id, order_id, event_status, shipments],
                        countdown=countdown,
                    )
                    return True

                log.process_status = WebhookProcessStatus.FAILED
                log.details = str(e)[:512]
                log.updated_at = datetime.now(timezone.utc)
                session.add(log)
                session.commit()
                logger.error(
                    "[Celery] notify_vfs exhausted %s retries for order %s", max_retries, order_id,
                )
                return False
        except Exception as log_err:
            logger.error("[Celery] Failed to update webhook log: %s", log_err)
            return False
