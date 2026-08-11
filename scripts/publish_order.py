"""
Test script for publish_order Celery task.

Usage:
    python publish_order.py                          # 用預設 order_id 測試
    python publish_order.py --order-id YOUR_ORDER_ID # 指定 order_id
    python publish_order.py --direct                 # 直接調用函數（不走 Celery）
    python publish_order.py --delay                  # 發送到 broker（需要 RabbitMQ + worker）
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


def get_order_data(order_id: str) -> dict:
    """Build the order_data payload for publish_order."""
    from sqlmodel import Session, select
    from app.core.database import engine
    from app.models.order import Order

    with Session(engine) as session:
        order = session.exec(
            select(Order).where(Order.order_id == order_id)
        ).first()

        if not order:
            logger.error(f"Order not found: {order_id}")
            sys.exit(1)

        logger.info(f"Found order: id={order.id}, order_id={order.order_id}, status={order.status.value}")

        return {
            "order_id": order.order_id,
            "source_order_id": order.source_order_id,
            "status": order.status.value,
            "created_at": order.created_at.isoformat() if order.created_at else None,
        }


def test_direct(order_id: str):
    """直接調用 publish_order 函數（不走 Celery broker）。"""
    from app.tasks.orders import publish_order

    order_data = get_order_data(order_id)
    logger.info(f"order_data: {order_data}")

    # 直接調用，bind=True 的 self 由 Celery 自動傳入
    result = publish_order(order_data)
    logger.info(f"Result: {result}")


def test_celery_apply(order_id: str):
    """透過 Celery apply() 同步執行（不走 broker，但走 Celery 框架）。"""
    from app.tasks.orders import publish_order

    order_data = get_order_data(order_id)
    logger.info(f"order_data: {order_data}")

    result = publish_order.apply(args=[order_data])
    logger.info(f"Status: {result.status}")
    logger.info(f"Result: {result.result}")


def test_celery_delay(order_id: str):
    """透過 Celery delay() 發送到 broker（需要 RabbitMQ + worker）。"""
    from app.tasks.orders import publish_order

    order_data = get_order_data(order_id)
    logger.info(f"order_data: {order_data}")

    async_result = publish_order.delay(order_data)
    logger.info(f"Task sent! ID: {async_result.id}")
    logger.info(f"Waiting for result...")
    result = async_result.get(timeout=300)  # 5 minutes timeout for file processing
    logger.info(f"Status: {async_result.status}")
    logger.info(f"Result: {result}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test publish_order task")
    parser.add_argument(
        "--order-id",
        type=str,
        default="test-order-001",
        help="Order ID to test (default: test-order-001)",
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Directly call the function (no Celery)",
    )
    parser.add_argument(
        "--delay",
        action="store_true",
        help="Send via Celery delay() (requires RabbitMQ + worker)",
    )
    args = parser.parse_args()

    logger.info(f"=== Testing publish_order for order_id={args.order_id} ===")

    if args.direct:
        logger.info("Mode: DIRECT call")
        test_direct(args.order_id)
    elif args.delay:
        logger.info("Mode: Celery delay() (async via broker)")
        test_celery_delay(args.order_id)
    else:
        logger.info("Mode: Celery apply() (sync, no broker)")
        test_celery_apply(args.order_id)
