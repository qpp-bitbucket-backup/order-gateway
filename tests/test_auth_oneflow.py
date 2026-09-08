"""Unit tests for OneFlow signed-header authentication (app/core/auth_oneflow.py)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.core.auth_oneflow import (
    SUPPORTED_ALGORITHM,
    build_auth_headers,
    build_string_to_sign,
    generate_signature,
    get_client_store_id,
    verify_oneflow_auth,
    _parse_iso_timestamp,
    _verify_timestamp,
)

SECRET = "test-secret"
TOKEN = "test-token"
# Known-answer vector: HMAC-SHA256("test-secret", "POST /api/order 2026-01-01T12:00:00.000Z")
KNOWN_DIGEST = "5eb43d34789cde45f542b7de491e960cf570ed1cfb5b29b8b12d1514685241ba"
TS = "2026-01-01T12:00:00.000Z"


def _fresh_timestamp(offset_seconds: int = 0) -> str:
    return (
        datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)
    ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _fake_request(method: str = "POST", path: str = "/api/order") -> SimpleNamespace:
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path))


def _valid_headers(ts: str = TS) -> dict:
    signature = generate_signature(SECRET, "POST", "/api/order", ts)
    return {
        "x_oneflow_authorization": f"{TOKEN}:{signature}",
        "x_oneflow_date": ts,
        "x_oneflow_algorithm": SUPPORTED_ALGORITHM,
    }


class TestStringToSign:
    """Test build_string_to_sign."""

    def test_format(self):
        assert build_string_to_sign("POST", "/api/order", TS) == f"POST /api/order {TS}"

    def test_method_uppercased(self):
        assert build_string_to_sign("post", "/api/order", TS) == f"POST /api/order {TS}"


class TestGenerateSignature:
    """Test HMAC-SHA256 signature generation (known-answer vector)."""

    def test_known_answer(self):
        assert generate_signature(SECRET, "POST", "/api/order", TS) == KNOWN_DIGEST

    def test_changes_with_secret(self):
        assert generate_signature("other-secret", "POST", "/api/order", TS) != KNOWN_DIGEST

    def test_changes_with_path(self):
        assert generate_signature(SECRET, "POST", "/api/other", TS) != KNOWN_DIGEST

    def test_changes_with_timestamp(self):
        other_ts = "2026-01-01T12:00:01.000Z"
        assert generate_signature(SECRET, "POST", "/api/order", other_ts) != KNOWN_DIGEST


class TestBuildAuthHeaders:
    """Test outbound auth header construction."""

    def test_contains_all_three_headers(self):
        headers = build_auth_headers(TOKEN, SECRET, "POST", "/api/order", timestamp=TS)
        assert set(headers) == {
            "x-oneflow-authorization",
            "x-oneflow-date",
            "x-oneflow-algorithm",
        }

    def test_values(self):
        headers = build_auth_headers(TOKEN, SECRET, "POST", "/api/order", timestamp=TS)
        assert headers["x-oneflow-authorization"] == f"{TOKEN}:{KNOWN_DIGEST}"
        assert headers["x-oneflow-date"] == TS
        assert headers["x-oneflow-algorithm"] == "SHA256"

    def test_default_timestamp_is_fresh(self):
        # The header is millisecond-truncated, so allow a small rounding
        # margin on the lower bound.
        before = datetime.now(timezone.utc) - timedelta(milliseconds=5)
        headers = build_auth_headers(TOKEN, SECRET, "POST", "/api/order")
        parsed = _parse_iso_timestamp(headers["x-oneflow-date"])
        after = datetime.now(timezone.utc) + timedelta(milliseconds=5)
        assert parsed is not None and before <= parsed <= after


class TestParseIsoTimestamp:
    """Test ISO 8601 timestamp parsing."""

    def test_z_suffix(self):
        parsed = _parse_iso_timestamp("2026-01-01T12:00:00.000Z")
        assert parsed == datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_explicit_offset(self):
        parsed = _parse_iso_timestamp("2026-01-01T20:00:00+08:00")
        assert parsed == datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def test_invalid_returns_none(self):
        assert _parse_iso_timestamp("not-a-timestamp") is None
        assert _parse_iso_timestamp("") is None


class TestVerifyTimestamp:
    """Test timestamp freshness window (±300s)."""

    def test_fresh(self):
        assert _verify_timestamp(_fresh_timestamp()) is True

    def test_future_within_window(self):
        assert _verify_timestamp(_fresh_timestamp(offset_seconds=200)) is True

    def test_expired(self):
        assert _verify_timestamp(_fresh_timestamp(offset_seconds=-301)) is False

    def test_far_future_rejected(self):
        assert _verify_timestamp(_fresh_timestamp(offset_seconds=301)) is False

    def test_naive_treated_as_utc(self):
        naive = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")
        assert _verify_timestamp(naive) is True

    def test_invalid(self):
        assert _verify_timestamp("garbage") is False


class TestVerifyOneflowAuth:
    """Test the FastAPI dependency end-to-end (DB lookup patched)."""

    def _call(self, headers: dict, request=None):
        return verify_oneflow_auth(
            request=request or _fake_request(),
            session=None,
            x_oneflow_authorization=headers.get("x_oneflow_authorization"),
            x_oneflow_date=headers.get("x_oneflow_date"),
            x_oneflow_algorithm=headers.get("x_oneflow_algorithm"),
        )

    @pytest.mark.parametrize("missing", ["authorization", "date", "algorithm"])
    def test_missing_header_rejected_401(self, missing):
        headers = _valid_headers(ts=_fresh_timestamp())
        headers.pop(f"x_oneflow_{missing}")
        with pytest.raises(HTTPException) as exc:
            self._call(headers)
        assert exc.value.status_code == 401

    def test_unsupported_algorithm_rejected_401(self):
        headers = _valid_headers(ts=_fresh_timestamp())
        headers["x_oneflow_algorithm"] = "MD5"
        with pytest.raises(HTTPException) as exc:
            self._call(headers)
        assert exc.value.status_code == 401

    def test_malformed_authorization_rejected_401(self):
        headers = _valid_headers(ts=_fresh_timestamp())
        headers["x_oneflow_authorization"] = "no-colon-here"
        with pytest.raises(HTTPException) as exc:
            self._call(headers)
        assert exc.value.status_code == 401

    def test_empty_token_or_signature_rejected_401(self):
        headers = _valid_headers(ts=_fresh_timestamp())
        headers["x_oneflow_authorization"] = ":sig-only"
        with pytest.raises(HTTPException) as exc:
            self._call(headers)
        assert exc.value.status_code == 401

    def test_unknown_token_rejected_403(self):
        headers = _valid_headers(ts=_fresh_timestamp())
        with patch("app.core.auth_oneflow._get_client_secret", return_value=None):
            with pytest.raises(HTTPException) as exc:
                self._call(headers)
        assert exc.value.status_code == 403

    def test_expired_timestamp_rejected_401(self):
        headers = _valid_headers(ts=_fresh_timestamp(offset_seconds=-600))
        with patch("app.core.auth_oneflow._get_client_secret", return_value=SECRET):
            with pytest.raises(HTTPException) as exc:
                self._call(headers)
        assert exc.value.status_code == 401

    def test_wrong_signature_rejected_403(self):
        ts = _fresh_timestamp()
        headers = {
            "x_oneflow_authorization": f"{TOKEN}:deadbeef",
            "x_oneflow_date": ts,
            "x_oneflow_algorithm": SUPPORTED_ALGORITHM,
        }
        with patch("app.core.auth_oneflow._get_client_secret", return_value=SECRET):
            with pytest.raises(HTTPException) as exc:
                self._call(headers)
        assert exc.value.status_code == 403

    def test_valid_signature_returns_token(self):
        headers = _valid_headers(ts=_fresh_timestamp())
        with patch("app.core.auth_oneflow._get_client_secret", return_value=SECRET):
            assert self._call(headers) == TOKEN

    def test_signature_is_path_sensitive(self):
        # A signature valid for /api/order must not verify for another path.
        ts = _fresh_timestamp()
        signature = generate_signature(SECRET, "POST", "/api/order", ts)
        headers = {
            "x_oneflow_authorization": f"{TOKEN}:{signature}",
            "x_oneflow_date": ts,
            "x_oneflow_algorithm": SUPPORTED_ALGORITHM,
        }
        with patch("app.core.auth_oneflow._get_client_secret", return_value=SECRET):
            with pytest.raises(HTTPException) as exc:
                self._call(headers, request=_fake_request(path="/api/order/other"))
        assert exc.value.status_code == 403


class TestGetClientStoreId:
    """Test the store-scoping wrapper around verify_oneflow_auth."""

    def test_returns_client_store_id(self):
        with patch(
            "app.core.auth_oneflow.verify_oneflow_auth", return_value=TOKEN
        ), patch(
            "app.core.auth_oneflow._get_client_by_token",
            return_value=SimpleNamespace(store_id="store-001"),
        ):
            assert get_client_store_id(
                request=_fake_request(),
                session=None,
                x_oneflow_authorization=f"{TOKEN}:sig",
                x_oneflow_date=_fresh_timestamp(),
                x_oneflow_algorithm=SUPPORTED_ALGORITHM,
            ) == "store-001"

    def test_bootstrap_credentials_return_none(self):
        with patch(
            "app.core.auth_oneflow.verify_oneflow_auth", return_value=TOKEN
        ), patch(
            "app.core.auth_oneflow._get_client_by_token", return_value=None
        ):
            assert get_client_store_id(
                request=_fake_request(),
                session=None,
                x_oneflow_authorization=f"{TOKEN}:sig",
                x_oneflow_date=_fresh_timestamp(),
                x_oneflow_algorithm=SUPPORTED_ALGORITHM,
            ) is None
