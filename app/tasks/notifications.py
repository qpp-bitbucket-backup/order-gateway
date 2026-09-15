"""Outbound notification Celery tasks."""
import json
import logging
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from sqlmodel import Session, select

from app.core.celery import celery_app
from app.core.config import settings
from app.core.database import engine
from app.core.rabbitmq import QUEUE_ORDER_NOTIFYING
from app.core.sentry_alerts import ALERTS, capture_integration_alert
from app.models.client import Client
from app.models.notification import NotificationEmailLog, NotificationLevel
from app.models.order import Order, OrderStatus
from app.models.webhook_log import WebhookLog, WebhookProcessStatus
from app.services.email import email_service
from app.services.email_templates import render_order_status_email
from app.services.oms import oms_service
from app.services.order_notifications import resolve_notification_recipients
from app.services.vfs import vfs_service
from app.tasks.orders import _exponential_backoff

logger = logging.getLogger(__name__)


def _capture_notify_alert(channel: str, alert_key: str, order_id: Optional[str] = None, **format_args) -> None:
    """Capture a notify_oms/notify_vfs failure to Sentry.

    ``channel`` ("oms"/"vfs") plus ``failure_type`` (embedded in the alert
    definition) lets alert rules target either postback channel independently.
    """
    capture_integration_alert(
        ALERTS[channel][alert_key], order_id=order_id,
        format_args={"order_id": order_id, **format_args},
        order_queue=QUEUE_ORDER_NOTIFYING, channel=channel,
    )


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
                _capture_notify_alert("oms", "ORDER_NOT_FOUND", order_id, webhook_log_id=webhook_log_id)
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
            _capture_notify_alert("oms", "REJECTED", order_id, error_message=result.get('message', 'OMS returned failure'))
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
                        _capture_notify_alert("oms", "RETRY", order_id, exc=e)
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
                _capture_notify_alert("oms", "RETRY_EXHAUSTED", order_id, max_retries=max_retries, exc=e)
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
                _capture_notify_alert("vfs", "ORDER_NOT_FOUND", order_id, webhook_log_id=webhook_log_id)
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
            _capture_notify_alert("vfs", "REJECTED", order_id, error_message=result.get('message', 'VFS postback failed'))
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
                    if retry_count == 0:
                        _capture_notify_alert("vfs", "RETRY", order_id, exc=e)
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
                _capture_notify_alert("vfs", "RETRY_EXHAUSTED", order_id, max_retries=max_retries, exc=e)
                return False
        except Exception as log_err:
            logger.error("[Celery] Failed to update webhook log: %s", log_err)
            return False


def _retry_or_fail_email(session: Session, nlog: NotificationEmailLog, reason: str) -> bool:
    """Retry a failed NotificationEmailLog send with exponential backoff, or
    mark it FAILED once EMAIL_NOTIFY_RETRY_COUNT attempts are exhausted.

    Mirrors the retry bookkeeping of notify_oms/notify_vfs (retry_count on the
    log row + manual apply_async) — email_service.send_email never raises,
    so both the send-failure and exception paths funnel through here.
    """
    retry_count = nlog.retry_count or 0
    max_retries = settings.EMAIL_NOTIFY_RETRY_COUNT
    base_delay = settings.EMAIL_NOTIFY_RETRY_COUNTDOWN
    max_delay = settings.EMAIL_NOTIFY_RETRY_MAX_COUNTDOWN

    if retry_count < max_retries:
        countdown = _exponential_backoff(base_delay, retry_count, max_delay)
        nlog.retry_count = retry_count + 1
        nlog.details = reason[:512]
        nlog.updated_at = datetime.now(timezone.utc)
        session.add(nlog)
        session.commit()
        logger.warning(
            "[Celery] notify_order_status_email failed for log %s (attempt %s/%s), retrying in %ss",
            nlog.id, retry_count + 1, max_retries, countdown,
        )
        notify_order_status_email.apply_async(args=[nlog.id], countdown=countdown)
        return True

    nlog.process_status = WebhookProcessStatus.FAILED
    nlog.details = reason[:512]
    nlog.updated_at = datetime.now(timezone.utc)
    session.add(nlog)
    session.commit()
    logger.error(
        "[Celery] notify_order_status_email exhausted %s retries for log %s",
        max_retries, nlog.id,
    )
    return False


@celery_app.task(
    name="tasks.notifications.notify_order_status_email",
    queue=QUEUE_ORDER_NOTIFYING,
)
def notify_order_status_email(log_id: int) -> bool:
    """
    Send the level-based order status change notification email.

    Resolves recipients from the client's ``notification_config`` for the
    transition's level and sends one email per address via SendGrid:

    - Level disabled / unconfigured / no addresses -> log ``skipped``.
    - SENDGRID_API_KEY missing -> ``skipped`` (retrying cannot fix it).
    - All sends succeed -> ``processed``.
    - Any send fails -> exponential backoff retry up to
      ``EMAIL_NOTIFY_RETRY_COUNT`` attempts, then ``failed``. A retried run
      re-sends to every recipient (email_service has no per-address idempotency)
      — for WARNING/ERROR alerts a duplicate is preferable to a lost one.
    """
    logger.info("[Celery] notify_order_status_email: log_id=%s", log_id)

    try:
        with Session(engine) as session:
            nlog = session.exec(
                select(NotificationEmailLog).where(NotificationEmailLog.id == log_id)
            ).first()
            if not nlog:
                logger.error("[Celery] NotificationEmailLog not found: %s", log_id)
                return False

            # The order/store data lives on the log row itself; the order is
            # only read for the store fallback (deleted orders still notify)
            # and the cooling-off duration snapshot on COOLING_OFF entries.
            store_id = nlog.store_id
            order = None
            need_order = bool(nlog.order_id) and (
                not store_id or nlog.to_status == OrderStatus.COOLING_OFF.value
            )
            if need_order:
                order = session.exec(
                    select(Order).where(Order.order_id == nlog.order_id)
                ).first()
                if not store_id:
                    store_id = order.store_id if order else None

            client = None
            if store_id:
                client = session.exec(
                    select(Client).where(Client.store_id == store_id)
                ).first()

            level = NotificationLevel(nlog.level)
            recipients = resolve_notification_recipients(
                client.notification_config if client else None, level,
            )
            nlog.recipients = recipients

            if not recipients:
                nlog.process_status = WebhookProcessStatus.SKIPPED
                nlog.details = (
                    f"Level '{nlog.level}' not enabled for store '{store_id}' "
                    f"(no client config or no recipients)"
                )
                nlog.updated_at = datetime.now(timezone.utc)
                session.add(nlog)
                session.commit()
                logger.info(
                    "[Celery] Status email skipped for log %s (level '%s' disabled for store '%s')",
                    log_id, nlog.level, store_id,
                )
                return True

            if not email_service.is_configured():
                nlog.process_status = WebhookProcessStatus.SKIPPED
                nlog.details = "SENDGRID_API_KEY is not configured"
                nlog.updated_at = datetime.now(timezone.utc)
                session.add(nlog)
                session.commit()
                logger.error("[Celery] SENDGRID_API_KEY not configured; cannot send status email for log %s", log_id)
                return False

            # "View Details" button target — only when the admin panel base
            # URL is configured; the path uses the internal order_id the admin
            # console routes on.
            view_url = ""
            if nlog.order_id and settings.ADMIN_BASE_URL:
                view_url = (
                    f"{settings.ADMIN_BASE_URL.rstrip('/')}/admin/orders/show/{nlog.order_id}"
                )

            subject, text_content, html_content = render_order_status_email(
                order_id=nlog.order_id or "",
                from_status=nlog.from_status or "",
                to_status=nlog.to_status,
                level=nlog.level,
                message=nlog.message or "",
                source_order_id=nlog.source_order_id or "",
                store_name=(client.name if client else "") or (store_id or ""),
                occurred_at=nlog.created_at.isoformat() if nlog.created_at else "",
                view_url=view_url,
                cooling_off_seconds=(
                    order.cooling_off_seconds
                    if order and nlog.to_status == OrderStatus.COOLING_OFF.value
                    else None
                ),
            )

            failed = [
                r for r in recipients
                if not email_service.send_email(r, subject, html_content, text_content)
            ]
            if not failed:
                nlog.process_status = WebhookProcessStatus.PROCESSED
                nlog.details = json.dumps({"sent_to": recipients})
                nlog.updated_at = datetime.now(timezone.utc)
                session.add(nlog)
                session.commit()
                logger.info("[Celery] Status email sent for log %s to %s", log_id, recipients)
                return True

            return _retry_or_fail_email(
                session, nlog, f"SendGrid send failed for: {', '.join(failed)}",
            )

    except Exception as e:
        logger.error(
            "[Celery] notify_order_status_email failed for log %s: %s",
            log_id, e, exc_info=True,
        )
        try:
            with Session(engine) as session:
                nlog = session.exec(
                    select(NotificationEmailLog).where(NotificationEmailLog.id == log_id)
                ).first()
                if not nlog:
                    return True
                return _retry_or_fail_email(session, nlog, str(e))
        except Exception as log_err:
            logger.error("[Celery] Failed to update notification email log: %s", log_err)
            return False
