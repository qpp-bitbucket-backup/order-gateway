"""
Real (non-mocked) round trip against Ivan's OMS mock API (API-002, HUB4
transport) — calls OMSService.update_order_status() directly, no Celery
broker involved.

Requires .env to have OMS_API_URL / OMS_APP_SECRET / OMS_SOURCE_APP /
OMS_INTERFACE_TYPE pointed at the mock (see .env's OMS Configuration block).

Usage:
    python scripts/test_oms_mock.py --order-id ORD-0000011 --status processing
"""
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger(__name__)

from sqlmodel import Session, select
from app.core.database import engine
from app.models.order import Order
from app.services.oms import oms_service


def main():
    parser = argparse.ArgumentParser(description="Test OMSService.update_order_status against the real mock")
    parser.add_argument("--order-id", type=str, required=True, help="Order.order_id to use")
    parser.add_argument("--status", type=str, default="processing", help="event_status to send")
    args = parser.parse_args()

    with Session(engine) as session:
        order = session.exec(select(Order).where(Order.order_id == args.order_id)).first()
        if not order:
            logger.error(f"Order not found: {args.order_id}")
            sys.exit(1)
        logger.info(f"Using order: order_id={order.order_id} source_order_id={order.source_order_id}")

        result = oms_service.update_order_status(order=order, event_status=args.status)

    logger.info(f"Result: {result}")


if __name__ == "__main__":
    main()
