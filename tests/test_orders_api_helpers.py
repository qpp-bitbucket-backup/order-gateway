"""Unit tests for pure helper functions in app/api/orders.py."""
from app.api.orders import (
    _EXTERNAL_STATUS_MAP,
    _enrich_order_data_with_status,
    _mask_email,
    _mask_pii,
    _mask_postcode,
    _build_masked_address,
    _clean_source,
    _strip_none,
    _with_parallel_card_barcodes,
)
from app.models.address import Address as AddressModel
from app.models.order import OrderStatus


class TestExternalStatusMap:
    """Every internal status must have an external API representation."""

    def test_covers_all_statuses(self):
        assert set(_EXTERNAL_STATUS_MAP) == set(OrderStatus)

    def test_key_mappings(self):
        assert _EXTERNAL_STATUS_MAP[OrderStatus.RECEIVED] == "received"
        assert _EXTERNAL_STATUS_MAP[OrderStatus.PENDING] == "received"
        assert _EXTERNAL_STATUS_MAP[OrderStatus.VALIDATED] == "dataready"
        assert _EXTERNAL_STATUS_MAP[OrderStatus.COOLING_OFF] == "dataready"
        assert _EXTERNAL_STATUS_MAP[OrderStatus.PROCESSING] == "dataready"
        assert _EXTERNAL_STATUS_MAP[OrderStatus.PRINTREADY] == "printready"
        assert _EXTERNAL_STATUS_MAP[OrderStatus.PRODUCED] == "produced"
        assert _EXTERNAL_STATUS_MAP[OrderStatus.SHIPPED] == "shipped"
        assert _EXTERNAL_STATUS_MAP[OrderStatus.CANCELLED] == "cancelled"
        assert _EXTERNAL_STATUS_MAP[OrderStatus.FAILED] == "error"
        assert _EXTERNAL_STATUS_MAP[OrderStatus.ERRORED] == "error"


class TestEnrichOrderDataWithStatus:
    """Test status injection into order_data responses."""

    def test_none_order_data_returns_status_only(self):
        assert _enrich_order_data_with_status(None, OrderStatus.RECEIVED) == {
            "status": "received"
        }

    def test_injects_external_status(self):
        result = _enrich_order_data_with_status(
            {"items": [1, 2]}, OrderStatus.PRINTED
        )
        assert result == {"items": [1, 2], "status": "printed"}

    def test_does_not_mutate_original(self):
        original = {"items": [1, 2]}
        _enrich_order_data_with_status(original, OrderStatus.RECEIVED)
        assert original == {"items": [1, 2]}


class TestWithParallelCardBarcodes:
    """Test barcode injection for parallel-card item lists."""

    def test_no_barcode_returns_original(self):
        assert _with_parallel_card_barcodes({"items": [{"sku": "A"}]}, None) == {
            "items": [{"sku": "A"}]
        }

    def test_no_items_returns_original(self):
        assert _with_parallel_card_barcodes({"items": []}, "123") == {"items": []}
        assert _with_parallel_card_barcodes({}, "123") == {}

    def test_injects_indexed_barcodes(self):
        order_data = {
            "items": [
                {"sku": "A", "barcode": "old"},
                {"sku": "B"},
            ]
        }
        result = _with_parallel_card_barcodes(order_data, "4935768626")
        assert result["items"][0]["barcode"] == "493576862601"
        assert result["items"][1]["barcode"] == "493576862602"

    def test_does_not_mutate_original(self):
        order_data = {"items": [{"sku": "A"}]}
        _with_parallel_card_barcodes(order_data, "123")
        assert order_data["items"][0] == {"sku": "A"}


class TestCleanSource:
    """Test removal of internal-only keys from the source dict."""

    def test_strips_internal_keys(self):
        assert _clean_source(
            {"name": "VFS", "submitted_at": "2026-01-01T00:00:00Z"}
        ) == {"name": "VFS"}

    def test_keeps_other_keys(self):
        assert _clean_source({"name": "VFS"}) == {"name": "VFS"}

    def test_none_and_empty(self):
        assert _clean_source(None) is None
        assert _clean_source({}) == {}


class TestStripNone:
    """Test recursive None pruning (SiteFlow responses never carry nulls)."""

    def test_strips_none_from_dict(self):
        assert _strip_none({"a": 1, "b": None}) == {"a": 1}

    def test_strips_none_from_nested(self):
        # Dict values are pruned; bare None *elements inside a list* are
        # kept — only dict-valued members are recursed into.
        assert _strip_none(
            {"a": None, "list": [1, None, {"x": None, "y": 2}]}
        ) == {"list": [1, None, {"y": 2}]}

    def test_scalars_passthrough(self):
        assert _strip_none("x") == "x"
        assert _strip_none(5) == 5


class TestMasking:
    """Test PII masking helpers used by the platform order details API."""

    def test_mask_pii_keeps_prefix(self):
        assert _mask_pii("John", 2) == "Jo***"

    def test_mask_pii_short_value(self):
        # Values at or below the visible threshold keep only the first char.
        assert _mask_pii("J", 2) == "J***"
        assert _mask_pii("Jo", 2) == "J***"

    def test_mask_pii_none(self):
        assert _mask_pii(None) is None
        assert _mask_pii("") == ""

    def test_mask_email(self):
        assert _mask_email("john.doe@example.com") == "jo***@example.com"

    def test_mask_email_without_domain(self):
        # No @ -> returned untouched (cannot split local/domain parts).
        assert _mask_email("johndoe") == "johndoe"

    def test_mask_email_none(self):
        assert _mask_email(None) is None

    def test_mask_postcode(self):
        assert _mask_postcode("999077") == "999***"

    def test_mask_postcode_short(self):
        assert _mask_postcode("12") == "1***"

    def test_mask_postcode_none(self):
        assert _mask_postcode(None) is None

    def test_build_masked_address(self):
        addr = AddressModel(
            first_name="John",
            last_name="Doe",
            phone="85212345678",
            mobile="85298765432",
            email="john.doe@example.com",
            address1="123 Nathan Road",
            address2=None,
            postcode="999077",
            city="Hong Kong",
            state=None,
            country="HK",
            company="Acme Ltd",
        )
        masked = _build_masked_address(addr)
        assert masked.first_name == "Jo***"
        assert masked.last_name == "Do***"
        assert masked.phone == "8521***"
        assert masked.email == "jo***@example.com"
        assert masked.address1 == "123 ***"
        assert masked.address2 is None
        assert masked.postcode == "999***"
        # Non-PII fields are passed through unmasked
        assert masked.city == "Hong Kong"
        assert masked.country == "HK"
        assert masked.company == "Acm***"
