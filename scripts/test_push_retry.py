"""
Local test for push_order's retry/alert logic (exponential backoff via
_exponential_backoff + _capture_push_alert), using a faked httpx response
sequence instead of hitting the real QPMN API.

Verifies:
  Scenario A (503 -> 503 -> success): exactly one Sentry "warning" on the
  first retry, no "error" alert, order ends PROCESSING.
  Scenario B (503 x QPMN_PUSH_RETRY_COUNT): exactly one Sentry "warning" on
  the first retry, exactly one Sentry "error" alert once the cap is hit,
  order ends FAILED, no further retry scheduled.

Does not touch RabbitMQ/Celery broker or the real QPMN API. Creates two
disposable Order rows in the local DB and deletes them when done.

Usage:
    python scripts/test_push_retry.py
"""
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger(__name__)

from sqlmodel import Session, select
from app.core.config import settings
from app.core.database import engine
from app.models.order import Order, OrderStatus
import app.tasks.orders as orders_module
from app.tasks.orders import push_order

MAX_PUSH_RETRIES = settings.QPMN_PUSH_RETRY_COUNT


def make_order(order_id: str) -> Order:
    with Session(engine) as session:
        existing = session.exec(select(Order).where(Order.order_id == order_id)).first()
        if existing:
            session.delete(existing)
            session.commit()

        order = Order(
            order_id=order_id,
            source_account="test-push-retry",
            source_order_id=order_id,
            status=OrderStatus.VALIDATED,
            order_data={},
            store_id="TEST-STORE",
        )
        session.add(order)
        session.commit()
        session.refresh(order)
        return order


def cleanup_order(order_id: str):
    with Session(engine) as session:
        order = session.exec(select(Order).where(Order.order_id == order_id)).first()
        if order:
            session.delete(order)
            session.commit()


def get_status(order_id: str) -> OrderStatus:
    with Session(engine) as session:
        return session.exec(select(Order).where(Order.order_id == order_id)).first().status


def make_fake_http_client(responses):
    """Return a mock httpx.Client(...) factory whose .post() consumes `responses`
    in order. Each item is either ("code", status_code, body_dict)."""
    queue = iter(responses)

    def fake_post(*args, **kwargs):
        _, status_code, body = next(queue)
        resp = MagicMock()
        resp.status_code = status_code
        resp.json.return_value = body
        resp.text = str(body)
        return resp

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.side_effect = fake_post
    return mock_client


def run_scenario(name: str, order_id: str, responses):
    captured = {"warning": [], "error": []}

    def fake_capture_message(message, level="info"):
        captured.setdefault(level, []).append(message)

    def fake_capture_exception(exc):
        captured.setdefault("error", []).append(str(exc))

    order = make_order(order_id)
    order_data = {
        "order_id": order.order_id,
        "source_order_id": order.source_order_id,
        "status": order.status.value,
        "created_at": None,
    }

    mock_client = make_fake_http_client(responses)

    with patch("app.tasks.orders.httpx.Client", return_value=mock_client), \
         patch("app.services.order.order_service.merge_order", return_value={"orderData": {}}), \
         patch("app.services.client.client_service.get_store_key_by_id", return_value="dummy-store-key"), \
         patch("app.tasks.orders.sentry_sdk.capture_message", side_effect=fake_capture_message), \
         patch("app.tasks.orders.sentry_sdk.capture_exception", side_effect=fake_capture_exception), \
         patch.object(orders_module.push_order, "apply_async",
                       side_effect=lambda args, countdown=None: push_order(*args)):
        result = push_order(order_data)

    final_status = get_status(order_id)
    cleanup_order(order_id)

    logger.info(f"=== Scenario {name} ===")
    logger.info(f"push_order() returned: {result}")
    logger.info(f"final order.status: {final_status.value}")
    logger.info(f"Sentry warnings ({len(captured['warning'])}): {captured['warning']}")
    logger.info(f"Sentry errors/alerts ({len(captured['error'])}): {captured['error']}")
    return result, final_status, captured


def main():
    logger.info(f"MAX_PUSH_RETRIES = {MAX_PUSH_RETRIES}")

    # Scenario A: 503 -> 503 -> success
    result_a, status_a, captured_a = run_scenario(
        "A (503 -> 503 -> success)",
        "TEST-PUSH-RETRY-RECOVER",
        [
            ("code", 503, {}),
            ("code", 503, {}),
            ("code", 200, {"success": True, "data": {"orderId": "MOCK-RECOVER-1"}}),
        ],
    )
    ok_a = (
        result_a is True
        and status_a == OrderStatus.PROCESSING
        and len(captured_a["warning"]) == 1
        and len(captured_a["error"]) == 0
    )
    logger.info(f"Scenario A PASS: {ok_a}")

    # Scenario B: 503 x MAX_PUSH_RETRIES (exhausts the cap)
    _, status_b, captured_b = run_scenario(
        "B (503 x MAX_PUSH_RETRIES, exhausted)",
        "TEST-PUSH-RETRY-EXHAUST",
        [("code", 503, {}) for _ in range(MAX_PUSH_RETRIES)],
    )
    # Note: the top-level push_order() call only reflects its own attempt
    # (retry #1, scheduled successfully -> True), not the eventual outcome of
    # the recursive chain -- same as real Celery apply_async() being
    # fire-and-forget. The real outcome is order.status + Sentry captures.
    ok_b = (
        status_b == OrderStatus.FAILED
        and len(captured_b["warning"]) == 1
        and len(captured_b["error"]) == 1
    )
    logger.info(f"Scenario B PASS: {ok_b}")

    print()
    logger.info(f"=== Overall: {'PASS' if ok_a and ok_b else 'FAIL'} ===")


if __name__ == "__main__":
    main()
