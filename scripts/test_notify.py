"""
Test script for notify_oms / notify_vfs Celery tasks.

Usage:
    python scripts/test_notify.py --webhook-log-id 85 --order-id <order_id> --status printed --task oms
    python scripts/test_notify.py --webhook-log-id 86 --order-id <order_id> --status printed --task vfs
    python scripts/test_notify.py ... --task oms --shipments-json '[{"trackingNumber":"SF123","carrierName":"SF Express","service":"Next Day Air","trackingUrl":"https://...","shipDate":"2026-08-06T12:00:00.000Z"}]'
    python scripts/test_notify.py ... --delay   # send via broker (needs a worker)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import argparse
import json
import logging

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


def test_direct(task_name: str, webhook_log_id: int, order_id: str, event_status: str, shipments=None):
    """Directly call the task function (no Celery broker)."""
    from app.tasks.notifications import notify_oms, notify_vfs

    if task_name == "oms":
        result = notify_oms(webhook_log_id=webhook_log_id, order_id=order_id, event_status=event_status, shipments=shipments)
    else:
        result = notify_vfs(webhook_log_id=webhook_log_id, order_id=order_id, event_status=event_status)
    logger.info(f"Result: {result}")


def test_celery_delay(task_name: str, webhook_log_id: int, order_id: str, event_status: str, shipments=None):
    """Send via Celery delay() (requires RabbitMQ + a worker consuming order_notifying)."""
    from app.tasks.notifications import notify_oms, notify_vfs

    if task_name == "oms":
        async_result = notify_oms.delay(webhook_log_id=webhook_log_id, order_id=order_id, event_status=event_status, shipments=shipments)
    else:
        async_result = notify_vfs.delay(webhook_log_id=webhook_log_id, order_id=order_id, event_status=event_status)
    logger.info(f"Task sent! ID: {async_result.id}")
    result = async_result.get(timeout=60)
    logger.info(f"Result: {result}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test notify_oms/notify_vfs tasks")
    parser.add_argument("--webhook-log-id", type=int, required=True)
    parser.add_argument("--order-id", type=str, required=True)
    parser.add_argument("--status", type=str, required=True, help="event_status, e.g. order_item_produced")
    parser.add_argument("--task", type=str, choices=["oms", "vfs"], required=True)
    parser.add_argument("--shipments-json", type=str, default=None, help="JSON list of shipment objects (oms task only)")
    parser.add_argument("--delay", action="store_true", help="Send via Celery delay() (requires a worker)")
    args = parser.parse_args()

    shipments = json.loads(args.shipments_json) if args.shipments_json else None

    logger.info(f"=== Testing notify_{args.task} for webhook_log_id={args.webhook_log_id} ===")

    if args.delay:
        test_celery_delay(args.task, args.webhook_log_id, args.order_id, args.status, shipments)
    else:
        test_direct(args.task, args.webhook_log_id, args.order_id, args.status, shipments)
