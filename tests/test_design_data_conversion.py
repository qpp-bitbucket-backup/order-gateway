"""Unit tests for design-data source selection and Open API conversion.

Locks the fixed behaviour:
- The design source follows the SKU's data (product_design_data preferred,
  legacy customize_project fallback), NOT the SKU's api_version.
- _convert_customize_to_product_design_data picks its path by the input
  structure ("designData" key -> pass through; legacy "designs" -> convert).
"""
import copy

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models.address import Address, AddressType
from app.models.order import Order
from app.models.product import Sku
from app.services.order import order_service

# --- Open API structure (sku.product_design_data) ---
OPEN_PDD = {
    "designData": [{
        "code": "mat-1",
        "views": [{
            "code": "front",
            "designs": [{"index": 0, "effectImages": [
                {"effect": "foil", "imageUrl": None},
            ]}],
        }],
    }],
}

# --- Legacy structure (sku.customize_project) ---
LEGACY_CP = {
    "designs": [{
        "materialPath": "material/front",
        "side": "front",
        "pageContentDesigns": [
            {"pageContentIndex": 0, "effect": "foil", "image": None},
        ],
    }],
}

FILES = {"sku-1-0": [{"url": "https://oss/file-0.png"}]}


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


def _seed(session, *, product_design_data, customize_project, attribute_values=None,
          quantity=None, sku_api_version="open", second_sku_api_version=None):
    items = [{"sku": "PC-1"}]
    files = dict(FILES)
    if quantity is not None:
        items[0]["quantity"] = quantity
    if second_sku_api_version is not None:
        items.append({"sku": "PC-2"})
        files["sku-2-1"] = [{"url": "https://oss/file-2.png"}]
    session.add(Order(
        order_id="ord-1", source_account="qpmn", source_order_id="SO-1",
        store_id="store-1", order_data={"items": items},
        files=files,
    ))
    session.add(Sku(
        sku_id="sku-1", code="PC-1", product_id="prod-1", store_id="store-1",
        source_sku="PC-1", properties={"paper": "350gsm"},
        product_design_data=product_design_data,
        customize_project=customize_project,
        attribute_values=attribute_values,
        unit_price=10.0,
        api_version=sku_api_version,
    ))
    if second_sku_api_version is not None:
        session.add(Sku(
            sku_id="sku-2", code="PC-2", product_id="prod-1", store_id="store-1",
            source_sku="PC-2", properties={},
            product_design_data=None, customize_project=copy.deepcopy(LEGACY_CP),
            unit_price=10.0,
            api_version=second_sku_api_version,
        ))
    session.add(Address(
        order_id="ord-1", type=AddressType.DELIVERY,
        first_name="A", last_name="B", country="US", city="X", address1="1 Main St",
    ))
    session.commit()


def _prepare(session):
    return order_service._prepare_order_and_skus(session, "ord-1")


class TestSourceSelection:
    # Note: assertions compare content via the returned context, not object
    # identity — session.commit() expires the ORM objects, so _prepare
    # re-reads the JSON columns as fresh dicts.

    def test_product_design_data_preferred_when_present(self, session):
        pdd, cp = copy.deepcopy(OPEN_PDD), copy.deepcopy(LEGACY_CP)
        _seed(session, product_design_data=pdd, customize_project=cp)
        _, contexts, _ = _prepare(session)
        chosen = contexts[0]["customize_project"]
        # the SKU's Open API structure is preferred over the legacy cp ...
        assert "designData" in chosen
        assert chosen["designData"][0]["code"] == "mat-1"
        assert cp["designs"][0]["pageContentDesigns"][0]["image"] is None
        # ... and the Open API image injection replaced the placeholder URL
        effect = chosen["designData"][0]["views"][0]["designs"][0]["effectImages"][0]
        assert effect["imageUrl"] == "https://oss/file-0.png"

    def test_legacy_customize_project_used_as_fallback(self, session):
        cp = copy.deepcopy(LEGACY_CP)
        _seed(session, product_design_data=None, customize_project=cp)
        _, contexts, _ = _prepare(session)
        chosen = contexts[0]["customize_project"]
        # the legacy structure is used as fallback ...
        assert chosen["designs"][0]["materialPath"] == "material/front"
        # ... and the legacy image injection replaced the placeholder image
        pcd = chosen["designs"][0]["pageContentDesigns"][0]
        assert pcd["image"] == "https://oss/file-0.png"

    def test_placeholder_pdd_does_not_shadow_populated_customize_project(self, session):
        # a NON-NULL but empty product_design_data ({} or {"designData": []})
        # must not shadow a fully-populated legacy customize_project — the
        # design source is picked by content, not by field nullness
        cp = copy.deepcopy(LEGACY_CP)
        _seed(session, product_design_data={"designData": []}, customize_project=cp)
        _, contexts, _ = _prepare(session)
        chosen = contexts[0]["customize_project"]
        assert chosen["designs"][0]["materialPath"] == "material/front"
        assert chosen["designs"][0]["pageContentDesigns"][0]["image"] == "https://oss/file-0.png"


class TestApiVersionDispatch:
    """build_push_payload dispatches per the order's SKUs' api_version."""

    def _dispatch(self, session, monkeypatch, sku_api_version):
        _seed(session, product_design_data=None, customize_project=copy.deepcopy(LEGACY_CP),
              sku_api_version=sku_api_version)
        monkeypatch.setattr(order_service, "fetch_currency_from_qpmn", lambda store_id: "CNY")
        monkeypatch.setattr(order_service, "fetch_shipping_method_from_qpmn", lambda store_id: "Standard")
        return order_service.build_push_payload(session, "ord-1")

    def test_legacy_sku_builds_legacy_payload(self, session, monkeypatch):
        payload, api_version = self._dispatch(session, monkeypatch, "legacy")
        assert api_version == "legacy"
        assert "thirdOrderId" in payload

    def test_open_sku_builds_open_payload(self, session, monkeypatch):
        payload, api_version = self._dispatch(session, monkeypatch, "open")
        assert api_version == "open"
        assert "externalId" in payload

    def test_default_is_open(self, session):
        _seed(session, product_design_data=None, customize_project=copy.deepcopy(LEGACY_CP))
        assert order_service.resolve_order_api_version(session, "ord-1") == "open"

    def test_mixed_sku_versions_rejected(self, session):
        _seed(session, product_design_data=None, customize_project=copy.deepcopy(LEGACY_CP),
              sku_api_version="legacy", second_sku_api_version="open")
        with pytest.raises(ValueError, match="mixed"):
            order_service.resolve_order_api_version(session, "ord-1")


ATTRIBUTE_VALUES = [
    {"code": "P_Stock", "value": "300g藍芯纸"},
    {"code": "P_Size_of_deck", "value": 1},
    {"code": "P_Print_Type", "value": "四色印刷"},
]


class TestAttributeValues:
    def test_attribute_values_carried_in_context(self, session):
        _seed(session, product_design_data=None, customize_project=copy.deepcopy(LEGACY_CP),
              attribute_values=copy.deepcopy(ATTRIBUTE_VALUES))
        _, contexts, _ = _prepare(session)
        assert contexts[0]["attribute_values"] == ATTRIBUTE_VALUES

    def test_attribute_values_absent_when_sku_has_none(self, session):
        _seed(session, product_design_data=None, customize_project=copy.deepcopy(LEGACY_CP))
        _, contexts, _ = _prepare(session)
        assert contexts[0]["attribute_values"] is None

    def test_legacy_payload_includes_attribute_values(self, session, monkeypatch):
        _seed(session, product_design_data=None, customize_project=copy.deepcopy(LEGACY_CP),
              attribute_values=copy.deepcopy(ATTRIBUTE_VALUES))
        # _build_legacy_payload hits QPMN for currency/shipping — stub both
        monkeypatch.setattr(order_service, "fetch_currency_from_qpmn", lambda store_id: "CNY")
        monkeypatch.setattr(order_service, "fetch_shipping_method_from_qpmn", lambda store_id: "Standard")
        payload = order_service._build_legacy_payload(session, "ord-1")
        item = payload["items"][0]
        assert item["attributeValues"] == ATTRIBUTE_VALUES
        # forwarded verbatim next to properties / customizeProject
        assert item["properties"] == {"paper": "350gsm"}
        assert item["customizeProject"]["designs"]

    def test_legacy_payload_omits_attribute_values_when_missing(self, session, monkeypatch):
        _seed(session, product_design_data=None, customize_project=copy.deepcopy(LEGACY_CP))
        monkeypatch.setattr(order_service, "fetch_currency_from_qpmn", lambda store_id: "CNY")
        monkeypatch.setattr(order_service, "fetch_shipping_method_from_qpmn", lambda store_id: "Standard")
        payload = order_service._build_legacy_payload(session, "ord-1")
        assert "attributeValues" not in payload["items"][0]

    def test_size_of_deck_folds_qty_into_attribute_value(self, session, monkeypatch):
        # quantity 5 + a P_Size_of_deck attribute -> wire qty 1, value 5,
        # and order totals still price at 5 x unitPrice.
        _seed(session, product_design_data=None, customize_project=copy.deepcopy(LEGACY_CP),
              attribute_values=copy.deepcopy(ATTRIBUTE_VALUES), quantity=5)
        monkeypatch.setattr(order_service, "fetch_currency_from_qpmn", lambda store_id: "CNY")
        monkeypatch.setattr(order_service, "fetch_shipping_method_from_qpmn", lambda store_id: "Standard")
        payload = order_service._build_legacy_payload(session, "ord-1")
        item = payload["items"][0]
        assert item["qty"] == 1
        size_attr = next(a for a in item["attributeValues"] if a["code"] == "P_Size_of_deck")
        assert size_attr["value"] == 5
        # other attributes pass through untouched
        stock_attr = next(a for a in item["attributeValues"] if a["code"] == "P_Stock")
        assert stock_attr["value"] == "300g藍芯纸"
        totals = {t["name"]: t["value"] for t in payload["orderTotals"]}
        assert totals["SUBTOTAL"] == 50.0  # 5 decks x 10.0, not 1 x 10.0
        assert totals["ORDER_TOTAL"] == 50.0

    def test_qty_unchanged_without_size_of_deck_attribute(self, session, monkeypatch):
        attrs = [{"code": "P_Stock", "value": "300g藍芯纸"}]
        _seed(session, product_design_data=None, customize_project=copy.deepcopy(LEGACY_CP),
              attribute_values=attrs, quantity=5)
        monkeypatch.setattr(order_service, "fetch_currency_from_qpmn", lambda store_id: "CNY")
        monkeypatch.setattr(order_service, "fetch_shipping_method_from_qpmn", lambda store_id: "Standard")
        payload = order_service._build_legacy_payload(session, "ord-1")
        item = payload["items"][0]
        assert item["qty"] == 5
        assert item["attributeValues"][0]["value"] == "300g藍芯纸"
        totals = {t["name"]: t["value"] for t in payload["orderTotals"]}
        assert totals["SUBTOTAL"] == 50.0


class TestConversion:
    def test_open_structure_passes_through(self):
        out = order_service._convert_customize_to_product_design_data(
            copy.deepcopy(OPEN_PDD), {"paper": "350gsm"},
        )
        assert out == {"designData": OPEN_PDD["designData"]}

    def test_legacy_designs_converted(self):
        # simulate the post-injection state: image URL already filled in
        cp = copy.deepcopy(LEGACY_CP)
        cp["designs"][0]["pageContentDesigns"][0]["image"] = "https://oss/file-0.png"
        out = order_service._convert_customize_to_product_design_data(
            cp, {"paper": "350gsm"},
        )
        dd = out["designData"]
        assert len(dd) == 1
        assert dd[0]["views"][0]["code"] == "front"
        effect_images = dd[0]["views"][0]["designs"][0]["effectImages"]
        assert effect_images == [{"effect": "foil", "imageUrl": "https://oss/file-0.png"}]
        assert out["designAttributeValues"] == [{"code": "paper", "value": "350gsm"}]

    def test_effect_image_requires_both_effect_and_image(self):
        cp = {"designs": [{
            "materialPath": "m", "side": "front",
            "pageContentDesigns": [
                {"pageContentIndex": 0, "effect": "foil"},          # no image
                {"pageContentIndex": 1, "image": "u"},              # no effect
                {"pageContentIndex": 2, "effect": "e", "image": "u"},  # both
            ],
        }]}
        out = order_service._convert_customize_to_product_design_data(cp, {})
        designs = out["designData"][0]["views"][0]["designs"]
        assert designs[0]["effectImages"] == []
        assert designs[1]["effectImages"] == []
        assert designs[2]["effectImages"] == [{"effect": "e", "imageUrl": "u"}]
