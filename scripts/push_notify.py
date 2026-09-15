"""
Re-dispatch order status notification emails.

Resends one or more notification_email_logs rows by id via the
notify_order_status_email task — e.g. rows stuck at "received" (recipients
NULL) because the task message was dropped before the task_routes fix, or
retries that exhausted while SendGrid was misconfigured.

Usage:
    python scripts/push_notify.py 2                # 經 Celery 派發 log_id=2（需要 RabbitMQ + worker）
    python scripts/push_notify.py 2 5 7            # 多個 id
    python scripts/push_notify.py --direct 2       # 本地直接執行（不經 broker，立即發送）
    python scripts/push_notify.py --list-stuck     # 列出所有卡在 received 的行
"""
import os
import sys

# Add project root to Python path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


def _format_row(nlog) -> str:
    return (
        f"id={nlog.id} order={nlog.order_id} source={nlog.source_order_id} "
        f"store={nlog.store_id} {nlog.from_status}->{nlog.to_status} "
        f"level={nlog.level} status={nlog.process_status.value} "
        f"retry={nlog.retry_count} recipients={nlog.recipients}"
    )


def get_log(log_id: int):
    """Load a NotificationEmailLog row (exits when not found)."""
    from sqlmodel import Session
    from app.core.database import engine
    from app.models.notification import NotificationEmailLog

    with Session(engine) as session:
        nlog = session.get(NotificationEmailLog, log_id)
        if not nlog:
            logger.error(f"NotificationEmailLog not found: id={log_id}")
            sys.exit(1)
        logger.info(f"Current row: {_format_row(nlog)}")
        return nlog


def list_stuck() -> None:
    """List rows still at 'received' (task never consumed them)."""
    from sqlmodel import Session, select
    from app.core.database import engine
    from app.models.notification import NotificationEmailLog
    from app.models.webhook_log import WebhookProcessStatus

    with Session(engine) as session:
        rows = session.exec(
            select(NotificationEmailLog).where(
                NotificationEmailLog.process_status == WebhookProcessStatus.RECEIVED
            )
        ).all()
    if not rows:
        logger.info("No stuck (received) rows.")
        return
    for nlog in rows:
        logger.info(_format_row(nlog))
    logger.info(f"{len(rows)} stuck row(s) — re-dispatch with: python scripts/push_notify.py <id>...")


def show_final_state(log_id: int, label: str = "Final") -> None:
    """Re-read the row after the task ran and log its outcome."""
    from sqlmodel import Session
    from app.core.database import engine
    from app.models.notification import NotificationEmailLog

    with Session(engine) as session:
        nlog = session.get(NotificationEmailLog, log_id)
        logger.info(f"{label}: {_format_row(nlog) if nlog else 'row deleted'}")


def run_direct(log_id: int) -> None:
    """直接在本進程執行任務（不經 Celery broker）——立即發送。"""
    from app.tasks.notifications import notify_order_status_email

    get_log(log_id)
    logger.info("Running notify_order_status_email locally (sends immediately)...")
    ok = notify_order_status_email(log_id)
    logger.info(f"Result: {ok}")
    show_final_state(log_id)


def run_delay(log_id: int) -> None:
    """經 Celery delay() 派發給 worker（需要 RabbitMQ + worker 在線）。"""
    from app.tasks.notifications import notify_order_status_email

    get_log(log_id)
    async_result = notify_order_status_email.delay(log_id=log_id)
    logger.info(f"Task sent! ID: {async_result.id}")
    try:
        result = async_result.get(timeout=60)
        logger.info(f"Status: {async_result.status}, result: {result}")
    except Exception as e:
        logger.warning(
            f"Could not wait for result ({e}) — the task was dispatched; "
            "check notification_email_logs / worker logs for the outcome."
        )
    show_final_state(log_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Re-send order status notification emails by notification_email_logs id"
    )
    parser.add_argument(
        "ids",
        type=int,
        nargs="*",
        help="One or more notification_email_logs ids to re-send",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Run the task locally (no Celery broker; sends immediately)",
    )
    parser.add_argument(
        "--list-stuck",
        action="store_true",
        help="List rows stuck at 'received' (never consumed) and exit",
    )
    args = parser.parse_args()

    if args.list_stuck:
        list_stuck()
        sys.exit(0)

    if not args.ids:
        parser.error("provide at least one log id, or use --list-stuck")

    for log_id in args.ids:
        logger.info(f"=== Re-sending notification email for log_id={log_id} ===")
        if args.direct:
            run_direct(log_id)
        else:
            run_delay(log_id)
