"""Unit tests for the client default_recipient_email fallback in OMS fetches.

OMS address payloads (API-001) do not always carry an email; when the
client (store) has ``default_recipient_email`` configured it is stored on
the delivery/billing Address instead.
"""
from unittest import mock

from app.models.address import AddressType
from app.models.client import Client
from app.services.oms import OMSService

_DEFAULT_EMAIL = "fallback@example.com"

_ADDR_DATA_WITH_EMAIL = {
    "country": "CN",
    "state": "Guangdong",
    "city": "Dongguan",
    "address1": "Dongshan Industrial District",
    "postcode": "523619",
    "firstName": "Topps",
    "lastName": "Now",
    "email": "original@example.com",
}

_ADDR_DATA_NO_EMAIL = {
    "country": "CN",
    "state": "Guangdong",
    "city": "Dongguan",
    "address1": "Dongshan Industrial District",
    "postcode": "523619",
    "firstName": "Topps",
    "lastName": "Now",
    # no email key at all
}

_ADDR_DATA_EMPTY_EMAIL = {**_ADDR_DATA_WITH_EMAIL, "email": ""}


class TestMapToAddressDefaultEmail:
    """``_map_to_address`` email fallback precedence rules."""

    def test_oms_email_wins_over_default(self):
        addr = OMSService._map_to_address(
            _ADDR_DATA_WITH_EMAIL, "order-1", AddressType.DELIVERY,
            default_email=_DEFAULT_EMAIL,
        )
        assert addr.email == "original@example.com"

    def test_missing_email_filled_with_default(self):
        addr = OMSService._map_to_address(
            _ADDR_DATA_NO_EMAIL, "order-1", AddressType.DELIVERY,
            default_email=_DEFAULT_EMAIL,
        )
        assert addr.email == _DEFAULT_EMAIL

    def test_empty_email_filled_with_default(self):
        addr = OMSService._map_to_address(
            _ADDR_DATA_EMPTY_EMAIL, "order-1", AddressType.BILLING,
            default_email=_DEFAULT_EMAIL,
        )
        assert addr.email == _DEFAULT_EMAIL

    def test_no_default_keeps_email_none(self):
        addr = OMSService._map_to_address(
            _ADDR_DATA_NO_EMAIL, "order-1", AddressType.DELIVERY,
        )
        assert addr.email is None

    def test_no_email_and_no_default_stays_none(self):
        addr = OMSService._map_to_address(
            _ADDR_DATA_NO_EMAIL, "order-1", AddressType.DELIVERY,
            default_email=None,
        )
        assert addr.email is None


class TestResolveDefaultRecipientEmail:
    """``_resolve_default_recipient_email`` store lookup rules."""

    @staticmethod
    def _mock_session(client):
        session = mock.Mock()
        session.exec.return_value.first.return_value = client
        return session

    def test_returns_client_default_email(self):
        client = Client(store_id="store-1", default_recipient_email=_DEFAULT_EMAIL)
        result = OMSService._resolve_default_recipient_email(
            self._mock_session(client), "store-1"
        )
        assert result == _DEFAULT_EMAIL

    def test_client_without_default_returns_none(self):
        client = Client(store_id="store-1", default_recipient_email=None)
        result = OMSService._resolve_default_recipient_email(
            self._mock_session(client), "store-1"
        )
        assert result is None

    def test_unknown_store_returns_none(self):
        result = OMSService._resolve_default_recipient_email(
            self._mock_session(None), "store-404"
        )
        assert result is None

    def test_missing_store_id_skips_lookup(self):
        session = mock.Mock()
        assert OMSService._resolve_default_recipient_email(session, None) is None
        session.exec.assert_not_called()
