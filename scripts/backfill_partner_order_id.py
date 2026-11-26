"""One-off backfill of orders.partner_order_id from QPMN's store order detail.

Iterates every order that QPMN accepted (store_order_id present), is not in
'failed' status, and still has an empty partner_order_id; calls
order_service.query_qpmn_order() and persists data.logisticsInfo.orderNumber
when the detail carries one (same capture logic as the order_item_received
webhook path and POST /api/platform/orders/{order_id}/query).

Reads DATABASE_URL from .env via the app settings. Reruns are idempotent
(already-captured orders are filtered out). Run:

    python scripts/backfill_partner_order_id.py
"""
import os
import sys
import time

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlmodel import Session, select

from app.core.database import engine
from app.models.order import Order, OrderStatus
from app.services.order import order_service

# Politeness pause between QPMN calls (default rate limit is 20 rps)
_THROTTLE_SECONDS = 0.1


def main() -> None:
    captured = 0
    skipped_not_found = 0
    skipped_no_order_number = 0
    errors = 0

    with Session(engine) as session:
        orders = session.exec(
            select(Order).where(
                Order.partner_order_id.is_(None),  # type: ignore[union-attr]
                Order.status != OrderStatus.FAILED,
                Order.store_order_id.is_not(None),  # type: ignore[union-attr]
            )
        ).all()
        print(f"[backfill] {len(orders)} orders to check (partner_order_id empty, not failed, QPMN-accepted)")

        for order in orders:
            label = f"{order.source_order_id} (order {order.order_id[:8]}..., store_order_id={order.store_order_id})"
            try:
                result = order_service.query_qpmn_order(order)
            except Exception as exc:  # noqa: BLE001 — keep the run going
                errors += 1
                session.rollback()
                print(f"[ERROR] {label}: {exc}")
                time.sleep(_THROTTLE_SECONDS)
                continue

            if not result.get("success"):
                skipped_not_found += 1
                reason = "not found in QPMN" if result.get("not_found") else f"query failed: {result.get('error')}"
                print(f"[SKIP] {label}: {reason}")
                time.sleep(_THROTTLE_SECONDS)
                continue

            partner_order_number = result.get("partner_order_number")
            if partner_order_number:
                order.partner_order_id = partner_order_number
                session.add(order)
                session.commit()
                captured += 1
                print(f"[OK]   {label} -> partner_order_id={partner_order_number}")
            else:
                skipped_no_order_number += 1
                print(f"[SKIP] {label}: detail carries no logisticsInfo.orderNumber")

            time.sleep(_THROTTLE_SECONDS)

    print(
        f"[backfill] done: {captured} captured, "
        f"{skipped_not_found} not found/query failed, "
        f"{skipped_no_order_number} without orderNumber, {errors} errors"
    )
    print("BACKFILL_DONE")


if __name__ == "__main__":
    main()
