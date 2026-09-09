"""Unit tests for the Open API create-order payload (order_service._build_open_api_payload).

Locks the items[].externalId contract: it must carry the third-party SKU
reference from the original order payload (order_data.items[].sku), NOT the
internal sku_id — storeProductId keeps the internal id, and the top-level
externalId stays the gateway order id.
"""
from types import SimpleNamespace
from unittest.mock import patch

from app.models.order import Order, OrderType
from app.services.order import order_service


def _build(order=None, contexts=None):
    """Run _build_open_api_payload with DB prep and QPMN HTTP stubbed out."""
    order = order or Order(
        order_id="ord-1", source_account="qpmn", source_order_id="SO-1",
        type=OrderType.BASE_CARD,
    )
    contexts = contexts or [{
        "item": {"sku": "PC-123", "quantity": 2},
        "sku": SimpleNamespace(sku_id="sku-internal-1", unit_price=9.5),
        "properties": {},
        "customize_project": {},
    }]
    with patch.object(
        order_service, "_prepare_order_and_skus",
        return_value=(order, contexts, (None, None)),
    ), patch.object(
        order_service, "fetch_currency_from_qpmn", return_value="USD",
    ), patch.object(
        order_service, "fetch_shipping_method_from_qpmn",
        return_value="Standard",
    ), patch.object(
        order_service, "_address_to_open_api_payload", return_value={},
    ), patch.object(
        order_service, "_convert_customize_to_product_design_data",
        return_value={"design": "x"},
    ):
        return order_service._build_open_api_payload(None, "ord-1")


def test_item_external_id_is_order_item_sku():
    payload = _build()
    item = payload["items"][0]
    # externalId echoes the original order_data.items[].sku reference
    assert item["externalId"] == "PC-123"
    # storeProductId keeps the internal sku_id
    assert item["storeProductId"] == "sku-internal-1"
    assert item["quantity"] == 2
    assert item["unitPrice"] == 9.5


def test_top_level_external_id_stays_order_id():
    payload = _build()
    assert payload["externalId"] == "ord-1"
    assert payload["externalOrderNumber"] == "SO-1"


def test_parallel_card_supplier_stock_no_unchanged():
    order = Order(
        order_id="ord-9", source_account="qpmn", source_order_id="SO-9",
        type=OrderType.PARALLEL_CARD, barcode="BC",
    )
    contexts = [
        {"item": {"sku": f"PC-{n}", "quantity": 1},
         "sku": SimpleNamespace(sku_id=f"sku-{n}", unit_price=1.0),
         "properties": {}, "customize_project": {}}
        for n in (1, 2)
    ]
    payload = _build(order=order, contexts=contexts)
    assert [li["supplierStockNo"] for li in payload["items"]] == ["BC01", "BC02"]
    assert [li["externalId"] for li in payload["items"]] == ["PC-1", "PC-2"]
