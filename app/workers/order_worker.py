"""Order processing worker - now powered by Celery.

The actual task logic lives in ``app.tasks.orders``.  This module is kept as a
convenience entry-point so you can still trigger tasks directly for testing::

    python -m app.workers.order_worker
"""
import logging

from app.tasks.orders import publish_order, validate_order, push_order

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  [%(levelname)s]  %(name)s - %(message)s",
)
logger = logging.getLogger(__name__)


if __name__ == "__main__":
    # Quick test: trigger the publish_order Celery task directly
    test_payload = {
        "order_id": "814e3b9f-d741-411f-bd08-8db139edd9e4",
        "source_order_id": "ORD-0000005",
        "status": "received",
        "created_at": "2026-07-06T08:40:21",
    }
    logger.info("Triggering publish_order task via Celery …")
    publish_order.apply_async(args=[test_payload])
    logger.info("Task dispatched. Check the Celery worker for progress.")
