"""
Tests for VFSService.send_status_postback()'s per-order postback (no auth).

Part 1 (mocked httpx, no network): verifies the per-order postbackAddress
lookup/skip logic and that the request is a plain unsigned JSON POST.

Part 2 (real network, opt-in): actually calls Ivan's mock
(order-uat.popprint.cn/mock/api/order/status/webhook) using an existing
order — confirmed distinct from the OMS endpoint and to require no auth.

Usage:
    python scripts/test_vfs_service.py                # mocked tests only
    python scripts/test_vfs_service.py --order-id <id> --status shipped  # + real mock call
"""
import argparse
import os
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s - %(message)s")
logger = logging.getLogger(__name__)

from app.services.vfs import vfs_service
from app.models.order import Order, OrderStatus


def test_no_postback_address():
    order = Order(
        order_id="test-vfs-no-url",
        source_account="t",
        source_order_id="SRC-NO-URL",
        status=OrderStatus.PRINTED,
        order_data={},
    )
    result = vfs_service.send_status_postback(order=order, event_status="printed")
    ok = result.get("success") is False
    logger.info(f"[no postbackAddress] result={result} PASS={ok}")
    return ok


def test_with_postback_address_and_shipments():
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs.get("json")
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"success": True, "errorCode": None, "errorMsg": None}
        return resp

    mock_client = MagicMock()
    mock_client.__enter__.return_value = mock_client
    mock_client.__exit__.return_value = False
    mock_client.post.side_effect = fake_post

    order = Order(
        order_id="test-vfs-with-url",
        source_account="t",
        source_order_id="SRC-WITH-URL",
        status=OrderStatus.SHIPPED,
        order_data={"postbackAddress": "https://example.com/vfs-callback"},
    )
    shipments = [{
        "trackingNumber": "T1",
        "carrierName": "C1",
        "service": "S1",
        "trackingUrl": "https://t",
        "shipDate": "2026-08-06T12:00:00.000Z",
    }]

    with patch("app.services.vfs.httpx.Client", return_value=mock_client):
        result = vfs_service.send_status_postback(order=order, event_status="shipped", shipments=shipments)

    ok = (
        result.get("success") is True
        and captured["url"] == "https://example.com/vfs-callback"
        and captured["json"]["shipments"] == shipments
    )
    logger.info(f"[with postbackAddress] posted to {captured['url']}, payload={captured['json']}")
    logger.info(f"[with postbackAddress] PASS={ok}")
    return ok


def test_real_mock(order_id: str, status: str):
    """Opt-in: actually call the mock with a real order (postbackAddress must
    already be set to the mock URL on that order)."""
    from sqlmodel import Session, select
    from app.core.database import engine

    with Session(engine) as session:
        order = session.exec(select(Order).where(Order.order_id == order_id)).first()
        if not order:
            logger.error(f"Order not found: {order_id}")
            return False
        result = vfs_service.send_status_postback(order=order, event_status=status)

    logger.info(f"[real mock] result={result}")
    return result.get("success") is True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test VFSService")
    parser.add_argument("--order-id", type=str, default=None, help="Also test against the real mock with this order")
    parser.add_argument("--status", type=str, default="printed")
    args = parser.parse_args()

    results = [test_no_postback_address(), test_with_postback_address_and_shipments()]
    if args.order_id:
        results.append(test_real_mock(args.order_id, args.status))

    print()
    logger.info(f"=== Overall: {'PASS' if all(results) else 'FAIL'} ===")
