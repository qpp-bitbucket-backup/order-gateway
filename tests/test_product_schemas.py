"""Unit tests for product/SKU schemas (app/schemas/product.py)."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.product import (
    Product,
    ProductComponent,
    ProductsListResponse,
    Sku,
    SkusListResponse,
    SkuUpdateRequest,
    SkuUpdateResponse,
)


class TestProductComponent:
    def test_defaults(self):
        comp = ProductComponent()
        assert comp.code is None
        assert comp.required is False
        assert comp.attributes is None

    def test_full_payload(self):
        comp = ProductComponent(code="cover", type="paper", required=True, attributes={"gsm": 300})
        assert comp.attributes["gsm"] == 300


class TestProduct:
    """Product accepts QPMN's ``_id`` via alias and ``id`` by field name."""

    def test_accepts_underscore_id_alias(self):
        product = Product(_id="p-1", productCode="CARD-A5")
        assert product.id == "p-1"

    def test_accepts_field_name_via_populate_by_name(self):
        product = Product(id="p-2", productCode="CARD-A4")
        assert product.id == "p-2"

    def test_product_code_required(self):
        with pytest.raises(ValidationError):
            Product(_id="p-3")

    def test_optional_timestamps(self):
        product = Product(_id="p-4", productCode="CARD-A6")
        assert product.createdAt is None
        assert product.updatedAt is None
        assert product.components is None


class TestProductsListResponse:
    def test_pagination_defaults(self):
        resp = ProductsListResponse(success=True, count=0, data=[])
        assert resp.page == 1
        assert resp.pages == 1


class TestSku:
    def test_accepts_underscore_id_alias(self):
        sku = Sku(_id="s-1", code="CARD-A5-STD", productId="p-1")
        assert sku.id == "s-1"

    def test_defaults(self):
        sku = Sku(_id="s-2", code="SKU-X", productId="p-1")
        assert sku.active is True
        assert sku.maxPackageQuantity == 50
        assert sku.sourceSku is None

    def test_source_sku_max_length(self):
        with pytest.raises(ValidationError):
            Sku(_id="s-3", code="SKU-Y", productId="p-1", sourceSku="x" * 33)

    def test_unit_price_must_be_non_negative(self):
        with pytest.raises(ValidationError):
            Sku(_id="s-4", code="SKU-Z", productId="p-1", unitPrice=-0.01)

    def test_unit_cost_must_be_non_negative(self):
        with pytest.raises(ValidationError):
            Sku(_id="s-5", code="SKU-W", productId="p-1", unitCost=-1)

    def test_max_package_quantity_must_be_at_least_one(self):
        with pytest.raises(ValidationError):
            Sku(_id="s-6", code="SKU-V", productId="p-1", maxPackageQuantity=0)

    def test_dict_fields_roundtrip(self):
        sku = Sku(
            _id="s-7",
            code="SKU-U",
            productId="p-1",
            properties={"color": "black"},
            customizeProject={"projectId": 9},
            productDesignData={"url": "https://example.com/d"},
        )
        assert sku.properties == {"color": "black"}
        assert sku.customizeProject == {"projectId": 9}
        assert sku.productDesignData["url"].startswith("https://")


class TestSkusListResponse:
    def test_with_nested_skus(self):
        sku = Sku(_id="s-8", code="SKU-T", productId="p-1", createdAt=datetime.now(timezone.utc))
        resp = SkusListResponse(success=True, count=1, data=[sku])
        assert resp.data[0].code == "SKU-T"
        assert resp.page == 1


class TestSkuUpdateRequest:
    def test_all_fields_optional(self):
        req = SkuUpdateRequest()
        assert req.code is None
        assert req.maxPackageQuantity is None

    def test_partial_update(self):
        req = SkuUpdateRequest(unitPrice=12.5, active=False)
        assert req.unitPrice == 12.5
        assert req.active is False

    def test_validation_still_applies(self):
        with pytest.raises(ValidationError):
            SkuUpdateRequest(unitPrice=-5)
        with pytest.raises(ValidationError):
            SkuUpdateRequest(sourceSku="y" * 33)


class TestSkuUpdateResponse:
    def test_defaults(self):
        resp = SkuUpdateResponse()
        assert resp.success is True
        assert resp.message == "SKU updated successfully"
        assert resp.sku is None

    def test_with_sku(self):
        sku = Sku(_id="s-9", code="SKU-S", productId="p-1")
        resp = SkuUpdateResponse(sku=sku)
        assert resp.sku.id == "s-9"
