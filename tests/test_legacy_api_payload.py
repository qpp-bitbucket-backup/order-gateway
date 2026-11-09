"""Unit tests for the legacy create-order payload (order_service._build_legacy_payload).

Locks the full wire format of POST {QPMN_API_URL}/store/orders:
- Top level: thirdOrderId / thirdOrderNumber (with -vN for recreated
  orders) / currency / shippingMethod / paymentMethod / orderTotals.
- Line items: thirdOrderItemId / storeProductId / qty / unitPrice /
  properties / customizeProject (incl. comparisonThumbnail) /
  attributeValues / supplierStockNo (parallel cards).
- Addresses: full legacy mapping + billing fallback to delivery.

Runs against a real sqlite session so the Sku/Order/Address data path
(incl. design-file injection into customizeProject) is exercised; only the
QPMN HTTP calls (currency / shipping method) are stubbed.
"""
import copy

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.core.config import settings
from app.models.address import Address, AddressType
from app.models.order import Order, OrderType
from app.models.product import Sku
from app.services.order import order_service

LEGACY_CP = {
    "customizeType": "IMAGE",
    "designs": [{
        "side": "Card_Front",
        "materialPath": "58578097,58578094,58608514",
        "pageContentDesigns": [
            {"pageContentIndex": 0, "effect": "CMYK", "image": None},
        ],
    }],
}
ATTRIBUTE_VALUES = [
    {"code": "P_Stock", "value": "300g藍芯纸"},
    {"code": "P_Print_Type", "value": "四色印刷"},
]
FILES = {"1905904": [{"url": "https://oss/file-0.png"}, {"url": "https://oss/file-1.png"}]}

# Open API structure (sku.product_design_data) — what the product sync
# actually stores; customize_project stays NULL for such SKUs.
OPEN_PDD = {
    "designData": [{
        # base64("58578097,58578094,58608514")
        "code": "NTg1NzgwOTcsNTg1NzgwOTQsNTg2MDg1MTQ=",
        "views": [
            {"code": "50771146", "designs": [
                {"index": 0, "effectImages": [{"effect": "CMYK", "imageUrl": None}]},
            ]},
            {"code": "50771143", "designs": [
                {"index": 0, "effectImages": [{"effect": "CMYK", "imageUrl": None}]},
            ]},
        ],
    }],
}


@pytest.fixture(name="session")
def session_fixture():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        yield session


def _seed(
    session,
    *,
    quantity=2,
    with_billing=False,
    attribute_values=None,
    order_type=OrderType.BASE_CARD,
    barcode=None,
    version=1,
    open_design_data=None,
    strip_source_item_id=False,
):
    item = {"sku": "PC-1", "sourceItemId": 1905904}
    if quantity is not None:
        item["quantity"] = quantity
    files = FILES
    if strip_source_item_id:
        item.pop("sourceItemId")
        # without sourceItemId the design files fall back to the
        # {resolved_sku_id}-{item_index} composite key
        files = {"sku-1-0": FILES["1905904"]}
    session.add(Order(
        order_id="ord-1", source_account="qpmn", source_order_id="SO-1",
        store_id="store-1", order_data={"items": [item]}, files=files,
        type=order_type, barcode=barcode, version=version,
    ))
    session.add(Sku(
        sku_id="sku-1", code="PC-1", product_id="prod-1", store_id="store-1",
        source_sku="PC-1", properties={"back design mode": "same"},
        customize_project=None if open_design_data is not None else copy.deepcopy(LEGACY_CP),
        product_design_data=copy.deepcopy(open_design_data) if open_design_data is not None else None,
        attribute_values=copy.deepcopy(attribute_values) if attribute_values else None,
        unit_price=9.5,
    ))
    session.add(Address(
        order_id="ord-1", type=AddressType.DELIVERY,
        first_name="Ming", last_name="Huang", country="CN", state="Jiangsu",
        city="Nanjing", address1="630 Chang'an Road", address2="29F",
        postcode="210000", phone="025-1234567", mobile="13285207692",
        email="a@b.com", company="ACME",
    ))
    if with_billing:
        session.add(Address(
            order_id="ord-1", type=AddressType.BILLING,
            first_name="Yan", last_name="Chen", country="CN", state="Guangdong",
            city="Shenzhen", address1="452 Chang'an Road", address2="17F",
            postcode="518000",
        ))
    session.commit()


def _build(session, monkeypatch):
    monkeypatch.setattr(order_service, "fetch_currency_from_qpmn", lambda store_id: "CNY")
    monkeypatch.setattr(order_service, "fetch_shipping_method_from_qpmn", lambda store_id: "EMS")
    return order_service._build_legacy_payload(session, "ord-1")


class TestTopLevelFields:
    def test_full_top_level_contract(self, session, monkeypatch):
        _seed(session)
        payload = _build(session, monkeypatch)
        assert payload["thirdOrderId"] == "ord-1"
        # first submission carries the bare source_order_id (no -v1)
        assert payload["thirdOrderNumber"] == "SO-1"
        assert payload["currency"] == "CNY"
        assert payload["shippingMethod"] == "EMS"
        assert payload["paymentMethod"] == settings.QPMN_PAYMENT_METHOD
        assert [t["name"] for t in payload["orderTotals"]] == [
            "TAX", "SHIPPING", "SUBTOTAL", "ORDER_TOTAL",
        ]
        assert payload["orderTotals"][0]["value"] == 0.0
        assert payload["orderTotals"][1]["value"] == 0.0

    def test_third_order_number_versioned_for_recreated_order(self, session, monkeypatch):
        _seed(session, version=3)
        payload = _build(session, monkeypatch)
        assert payload["thirdOrderNumber"] == "SO-1-v3"
        # the platform order id stays the gateway order id
        assert payload["thirdOrderId"] == "ord-1"

    def test_order_totals_price_at_ordered_quantity(self, session, monkeypatch):
        _seed(session, quantity=2)
        payload = _build(session, monkeypatch)
        totals = {t["name"]: t["value"] for t in payload["orderTotals"]}
        assert totals["SUBTOTAL"] == 19.0   # 2 x 9.5
        assert totals["ORDER_TOTAL"] == 19.0


class TestLineItems:
    def test_full_line_item_contract(self, session, monkeypatch):
        _seed(session, attribute_values=ATTRIBUTE_VALUES)
        payload = _build(session, monkeypatch)
        item = payload["items"][0]
        # thirdOrderItemId carries the VFS order item's sourceItemId; only
        # storeProductId keeps the internal sku_id
        assert item["thirdOrderItemId"] == 1905904
        assert item["storeProductId"] == "sku-1"
        assert item["qty"] == 2
        assert item["unitPrice"] == 9.5
        assert item["properties"] == {"back design mode": "same"}
        assert item["attributeValues"] == ATTRIBUTE_VALUES
        # the uploaded design file was injected into pageContentDesigns.image
        pcd = item["customizeProject"]["designs"][0]["pageContentDesigns"][0]
        assert pcd["image"] == "https://oss/file-0.png"
        # comparisonThumbnail derives from the first file (QPMN preview URL)
        assert item["customizeProject"]["comparisonThumbnail"] == \
            "https://oss/file-0.png/320/320/png"

    def test_third_order_item_id_falls_back_to_sku_id(self, session, monkeypatch):
        # item without sourceItemId — pre-change wire format (internal sku_id)
        _seed(session, strip_source_item_id=True)
        payload = _build(session, monkeypatch)
        item = payload["items"][0]
        assert item["thirdOrderItemId"] == "sku-1"
        assert item["storeProductId"] == "sku-1"

    def test_qty_defaults_to_one_and_unit_price_to_zero(self, session, monkeypatch):
        # quantity omitted on the item, unit_price unset on the SKU
        _seed(session, quantity=None)
        sku = session.exec(select(Sku).where(Sku.sku_id == "sku-1")).first()
        sku.unit_price = None
        session.add(sku)
        session.commit()
        payload = _build(session, monkeypatch)
        item = payload["items"][0]
        assert item["qty"] == 1
        assert item["unitPrice"] == 0
        totals = {t["name"]: t["value"] for t in payload["orderTotals"]}
        assert totals["SUBTOTAL"] == 0.0

    def test_attribute_values_omitted_when_sku_has_none(self, session, monkeypatch):
        _seed(session)
        payload = _build(session, monkeypatch)
        assert "attributeValues" not in payload["items"][0]

    def test_supplier_stock_no_only_on_parallel_card(self, session, monkeypatch):
        _seed(session, order_type=OrderType.PARALLEL_CARD, barcode="4935768626")
        payload = _build(session, monkeypatch)
        # barcode + two-digit 1-based item index
        assert payload["items"][0]["supplierStockNo"] == "493576862601"

    def test_base_card_has_no_supplier_stock_no(self, session, monkeypatch):
        _seed(session, order_type=OrderType.BASE_CARD, barcode="BC")
        payload = _build(session, monkeypatch)
        assert "supplierStockNo" not in payload["items"][0]

    def test_open_structure_converted_to_legacy_customize_project(self, session, monkeypatch):
        # SKU carries ONLY product_design_data (Open structure) — customize_project
        # stays NULL, exactly what the product sync produces. The legacy payload
        # must still emit a full customizeProject, not a lone comparisonThumbnail.
        _seed(session, open_design_data=OPEN_PDD)
        payload = _build(session, monkeypatch)
        cp = payload["items"][0]["customizeProject"]
        assert cp["customizeType"] == "IMAGE"
        assert [d["side"] for d in cp["designs"]] == ["Card_Front", "Card_Back"]
        assert all(
            d["materialPath"] == "58578097,58578094,58608514" for d in cp["designs"]
        )
        # the injected design file URLs land on pageContentDesigns.image
        assert cp["designs"][0]["pageContentDesigns"] == [
            {"pageContentIndex": 0, "effect": "CMYK", "image": "https://oss/file-0.png"},
        ]
        assert cp["designs"][1]["pageContentDesigns"] == [
            {"pageContentIndex": 0, "effect": "CMYK", "image": "https://oss/file-1.png"},
        ]
        # comparisonThumbnail keeps coming from the first file
        assert cp["comparisonThumbnail"] == "https://oss/file-0.png/320/320/png"

    def test_open_structure_skips_views_without_image(self):
        # views whose effectImages carry no imageUrl (e.g. an unfilled label
        # placeholder) produce no design entry — tested directly on the
        # converter (the payload builder's file-count guard rejects such
        # under-filled orders earlier).
        pdd = copy.deepcopy(OPEN_PDD)
        pdd["designData"][0]["views"][1]["designs"][0]["effectImages"][0]["imageUrl"] = None
        pdd["designData"][0]["views"].append(
            {"code": "12345678", "designs": [
                {"index": None, "effectImages": [{"effect": "CMYK", "imageUrl": None}]},
            ]},
        )
        cp = order_service._convert_product_design_to_customize(pdd)
        assert [d["side"] for d in cp["designs"]] == ["Card_Front"]
        assert cp["designs"][0]["pageContentDesigns"][0]["image"] is not None


class TestAddresses:
    def test_delivery_address_full_mapping(self, session, monkeypatch):
        _seed(session, with_billing=True)
        payload = _build(session, monkeypatch)
        assert payload["deliveryAddress"] == {
            "country": "CN", "state": "Jiangsu", "city": "Nanjing",
            "address_1": "630 Chang'an Road", "address_2": "29F",
            "postcode": "210000", "first_name": "Ming", "last_name": "Huang",
            "phone": "025-1234567", "mobile": "13285207692",
            "email": "a@b.com", "company": "ACME",
        }
        assert payload["billingAddress"]["first_name"] == "Yan"
        assert payload["billingAddress"] != payload["deliveryAddress"]

    def test_billing_falls_back_to_delivery(self, session, monkeypatch):
        _seed(session, with_billing=False)
        payload = _build(session, monkeypatch)
        assert payload["billingAddress"] == payload["deliveryAddress"]


def test_dump_payload(session, monkeypatch):
    """Debug helper: print the built legacy payload.

    Normal runs stay silent (pytest captures stdout); inspect the payload with:

        python -m pytest tests/test_legacy_api_payload.py -k dump -s
    """
    import json
    _seed(session, attribute_values=ATTRIBUTE_VALUES, quantity=5)
    payload = _build(session, monkeypatch)
    print("\n=== Legacy API payload ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def test_dump_real_order_payload(order_id_option):
    """Build the legacy payload for a REAL order from the app database.

    Skipped unless --order-id is passed (so normal runs/CI are unaffected).
    Currency / shipping method are fetched from QPMN like a real push.

        python -m pytest tests/test_legacy_api_payload.py -k real -s --order-id=4fc3baae-9bde-4d69-a66e-593f0a201aea
    """
    import json
    order_id = order_id_option
    if not order_id:
        pytest.skip("--order-id not passed — add --order-id=<order_id> to dump that order's payload")

    from app.core.database import engine
    with Session(engine) as session:
        payload = order_service._build_legacy_payload(session, order_id)
    print(f"\n=== Legacy API payload (real order {order_id}) ===")
    print(json.dumps(payload, indent=2, ensure_ascii=False, default=str))
