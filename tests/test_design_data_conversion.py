"""Unit tests for design-data source selection and Open API conversion.

Locks the fixed behaviour:
- The design source follows the SKU's data (product_design_data preferred,
  legacy customize_project fallback), NOT QPMN_ORDER_API_VERSION.
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


def _seed(session, *, product_design_data, customize_project):
    session.add(Order(
        order_id="ord-1", source_account="qpmn", source_order_id="SO-1",
        store_id="store-1", order_data={"items": [{"sku": "PC-1"}]},
        files=FILES,
    ))
    session.add(Sku(
        sku_id="sku-1", code="PC-1", product_id="prod-1", store_id="store-1",
        source_sku="PC-1", properties={"paper": "350gsm"},
        product_design_data=product_design_data,
        customize_project=customize_project,
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
