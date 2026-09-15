"""Level-based order status change recording and email notification dispatch.

Every order status transition goes through ``record_status_change()``: it
sets the new status and appends a level-tagged entry to ``order.logs``
(NotificationLevel: info/warning/error) via the single write path
``OrderService.append_log``. ``enqueue_status_change_email()`` then persists
a pending ``NotificationEmailLog`` and dispatches the ``notify_order_status_email``
Celery task, which resolves the client's per-level recipients
(``clients.notification_config``) and sends the email via SendGrid —
unconfigured levels simply mark the log ``skipped``, so clients without
a config keep today's behaviour exactly.
"""
import logging
from typing import Any, Dict, List, Optional, Tuple

from sqlmodel import Session

from app.models.notification import (
    EMAIL_MUTED_STATUSES,
    NotificationEmailLog,
    NotificationLevel,
    STATUS_NOTIFICATION_LEVEL,
    status_notification_level,
)
from app.models.order import Order, OrderStatus
from app.models.webhook_log import WebhookProcessStatus

logger = logging.getLogger(__name__)


def record_status_change(
    order: Order,
    new_status: OrderStatus,
    action: str,
    message: str,
    worker: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Tuple[OrderStatus, NotificationLevel]:
    """
    Set ``order.status`` and append a level-tagged entry to ``order.logs``.

    Returns ``(previous_status, notification_level)``. Does NOT commit —
    callers keep their existing add/commit flow; once committed they should
    call ``enqueue_status_change_email()`` so the change is also dispatched
    as a per-level email notification.

    ``extra`` merges additional fields into the log entry (e.g. the webhook
    handler's ``event_status``); ``worker`` names the writer like the plain
    ``OrderService.append_log`` entries do.
    """
    # Lazy import: services.order imports this module at the top, so pulling
    # order_service here keeps the import graph acyclic (same pattern as the
    # notify_oms/notify_vfs lazy imports in tasks/orders.py).
    from app.services.order import order_service

    from_status = order.status
    order.status = new_status
    level = status_notification_level(new_status)
    order_service.append_log(
        order, action, message, level=level.value, worker=worker, extra=extra
    )
    return from_status, level


def enqueue_status_change_email(
    session: Session,
    order: Order,
    from_status: Optional[OrderStatus],
    to_status: OrderStatus,
    level: NotificationLevel,
    message: str,
) -> Optional[int]:
    """
    Persist a pending ``NotificationEmailLog`` for the transition and
    dispatch the ``notify_order_status_email`` task.

    ``from_status`` is ``None`` for a newly created order's first transition
    (rendered as "new" in the email); ``to_status`` is always concrete.

    Transitions into ``EMAIL_MUTED_STATUSES`` (PENDING / PROCESSING) return
    ``None`` before any DB write — order.logs keeps the entry, but no email
    is dispatched. COOLING_OFF is deliberately NOT muted: entering the
    cooling-off period is a notifyable event (its duration rides on
    ``order.cooling_off_seconds`` and is rendered in the email body).

    Call only AFTER the status change has been committed — the task re-reads
    the order and the client's ``notification_config`` from the DB, so no
    lookups happen here and unconfigured clients cost just one INSERT plus
    one task that immediately marks itself ``skipped``. Returns the log id.
    """
    if to_status in EMAIL_MUTED_STATUSES:
        logger.info(
            "[notify] Email dispatch muted for %s transition of order %s",
            to_status.value, order.order_id,
        )
        return None

    # Lazy import: tasks.notifications pulls in the task graph (and would
    # circularly import services through app.tasks.orders) — same pattern as
    # the notify_oms/notify_vfs lazy imports in tasks/orders.py.
    from app.tasks.notifications import notify_order_status_email

    log = NotificationEmailLog(
        order_id=order.order_id,
        source_order_id=order.source_order_id,
        store_id=order.store_id,
        from_status=from_status.value if from_status else None,
        to_status=to_status.value,
        level=level.value,
        message=(message or "")[:1000] or None,
        process_status=WebhookProcessStatus.RECEIVED,
    )
    session.add(log)
    session.commit()
    session.refresh(log)
    notify_order_status_email.delay(log_id=log.id)
    return log.id


def resolve_notification_recipients(
    notification_config: Optional[Dict[str, Any]],
    level: NotificationLevel,
) -> List[str]:
    """
    Email addresses subscribed to ``level`` per a client's config dict
    (``{"info"|"warning"|"error": {"enabled": bool, "emails": [...]}}``).

    Empty list when the config is missing, the level is disabled, or it has
    no addresses — the caller treats that as "nothing to send".
    """
    if not notification_config:
        return []
    setting = notification_config.get(level.value) or {}
    if not setting.get("enabled"):
        return []
    return list(setting.get("emails") or [])


def level_tag(level: NotificationLevel) -> str:
    """Uppercase level token for email subjects (info -> INFO)."""
    return level.value.upper()
