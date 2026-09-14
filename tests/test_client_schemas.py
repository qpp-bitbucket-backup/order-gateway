"""Unit tests for client schemas (app/schemas/client.py)."""
from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from app.schemas.client import (
    ClientCreatedResponse,
    ClientCreateRequest,
    ClientResponse,
    ClientSecretRevealRequest,
    ClientSecretRevealResponse,
    ClientSecretUpdateRequest,
    ClientStoreKeyRevealRequest,
    ClientStoreKeyRevealResponse,
    ClientStoreKeyUpdateRequest,
    ClientSummary,
    ClientUpdateRequest,
    ClientsListResponse,
    PlatformClientCreateRequest,
    PlatformSalesStatsResponse,
    PlatformSalesStatSummary,
)

_NAME = "Acme Print Store"
_STORE = "store-001"
_LONG_NAME = "x" * 129
_LONG_SECRET = "s" * 32


def _summary(**overrides) -> ClientSummary:
    """Build a valid ClientSummary, allowing per-test overrides."""
    defaults = dict(
        id=1,
        name=_NAME,
        store_id=_STORE,
        token="token-abc",
        description=None,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    defaults.update(overrides)
    return ClientSummary(**defaults)


class TestClientCreateRequest:
    """Admin-key create schema: name/store_id required, rest optional."""

    def test_minimal_valid(self):
        req = ClientCreateRequest(name=_NAME, store_id=_STORE)
        assert req.store_key is None
        assert req.description is None

    def test_full_payload(self):
        req = ClientCreateRequest(
            name=_NAME, store_id=_STORE, store_key="key-1", description="main client"
        )
        assert req.store_key == "key-1"

    def test_name_must_not_be_empty(self):
        with pytest.raises(ValidationError):
            ClientCreateRequest(name="", store_id=_STORE)

    def test_name_max_length_enforced(self):
        with pytest.raises(ValidationError):
            ClientCreateRequest(name=_LONG_NAME, store_id=_STORE)

    def test_store_id_required(self):
        with pytest.raises(ValidationError):
            ClientCreateRequest(name=_NAME)


class TestPlatformClientCreateRequest:
    """Platform API create schema: every field required, no defaults."""

    def test_all_fields_required(self):
        payload = dict(name=_NAME, store_id=_STORE, store_key="key-1", description="desc")
        assert PlatformClientCreateRequest(**payload).name == _NAME

    def test_missing_description_rejected(self):
        with pytest.raises(ValidationError):
            PlatformClientCreateRequest(name=_NAME, store_id=_STORE, store_key="key-1")

    def test_empty_store_key_rejected(self):
        with pytest.raises(ValidationError):
            PlatformClientCreateRequest(name=_NAME, store_id=_STORE, store_key="", description="d")


class TestClientSecretSchemas:
    """Reveal/update secret flows."""

    def test_reveal_request_requires_password(self):
        req = ClientSecretRevealRequest(store_id=_STORE, password="hunter2!")
        assert req.password == "hunter2!"
        with pytest.raises(ValidationError):
            ClientSecretRevealRequest(store_id=_STORE)

    def test_reveal_response_defaults_success_true(self):
        resp = ClientSecretRevealResponse(store_id=_STORE, secret="secret-value")
        assert resp.success is True

    def test_update_requires_32_char_secret(self):
        req = ClientSecretUpdateRequest(store_id=_STORE, secret=_LONG_SECRET)
        assert len(req.secret) == 32
        with pytest.raises(ValidationError):
            ClientSecretUpdateRequest(store_id=_STORE, secret="short")


class TestClientStoreKeySchemas:
    """Reveal/update store key flows."""

    def test_reveal_request_and_response(self):
        req = ClientStoreKeyRevealRequest(store_id=_STORE)
        resp = ClientStoreKeyRevealResponse(store_id=req.store_id, store_key="qpmn-key")
        assert resp.success is True
        assert resp.store_key == "qpmn-key"

    def test_update_rejects_empty_key(self):
        with pytest.raises(ValidationError):
            ClientStoreKeyUpdateRequest(store_id=_STORE, store_key="")


class TestClientUpdateRequest:
    """Partial update: everything optional, rotate_secret defaults False."""

    def test_empty_update_is_valid(self):
        req = ClientUpdateRequest()
        assert req.name is None
        assert req.is_active is None

    def test_rotate_secret_defaults_false(self):
        assert ClientUpdateRequest(name=_NAME).rotate_secret is False

    def test_partial_fields(self):
        req = ClientUpdateRequest(store_id=_STORE, is_active=False, rotate_secret=True)
        assert req.is_active is False
        assert req.rotate_secret is True


class TestClientResponses:
    """Nested response envelopes."""

    def test_summary_roundtrip(self):
        summary = _summary(id=7, name="Other Store")
        assert summary.id == 7

    def test_created_response_defaults_success(self):
        resp = ClientCreatedResponse(client=_summary(), secret="secret-value")
        assert resp.success is True

    def test_single_client_response(self):
        resp = ClientResponse(client=_summary())
        assert resp.success is True

    def test_list_response_shape(self):
        resp = ClientsListResponse(
            count=2, page=1, pages=1, data=[_summary(id=1), _summary(id=2)]
        )
        assert [c.id for c in resp.data] == [1, 2]


class TestPlatformSalesStats:
    """Platform sales report schemas."""

    def _stat_row(self, **overrides) -> PlatformSalesStatSummary:
        defaults = dict(
            statDate=date(2026, 9, 1),
            clientId=3,
            storeName=_NAME,
            storeId=_STORE,
            currency="USD",
            ordersRequested=10,
            ordersSubmitted=8,
            lineItemsCount=12,
            lineItemsQuantity=30,
            totalAmount=1500.5,
        )
        defaults.update(overrides)
        return PlatformSalesStatSummary(**defaults)

    def test_stat_row_fields(self):
        row = self._stat_row()
        assert row.statDate == date(2026, 9, 1)
        assert row.currency == "USD"

    def test_currency_is_optional(self):
        assert self._stat_row(currency=None).currency is None

    def test_stat_date_must_be_date(self):
        with pytest.raises(ValidationError):
            self._stat_row(statDate="not-a-date")

    def test_response_envelope(self):
        resp = PlatformSalesStatsResponse(
            count=1,
            startDate=date(2026, 9, 1),
            endDate=date(2026, 9, 2),
            data=[self._stat_row()],
        )
        assert resp.success is True
        assert len(resp.data) == 1
