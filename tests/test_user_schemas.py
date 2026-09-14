"""Unit tests for user schemas (app/schemas/user.py)."""
from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.models.user import UserRole
from app.schemas.user import (
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    TokenPayload,
    UserCreate,
    UserResponse,
    UserUpdate,
)


class TestUserCreate:
    def test_minimal_valid(self):
        user = UserCreate(username="alice", email="alice@example.com", password="secret1")
        assert user.role == UserRole.VIEWER
        assert user.full_name is None
        assert user.store_id is None

    def test_full_payload(self):
        user = UserCreate(
            username="bob",
            email="bob@example.com",
            password="secret2",
            full_name="Bob Builder",
            role=UserRole.ADMIN,
            store_id="store-9",
        )
        assert user.role == UserRole.ADMIN

    def test_username_min_length(self):
        with pytest.raises(ValidationError):
            UserCreate(username="ab", email="a@example.com", password="secret3")

    def test_password_min_length(self):
        with pytest.raises(ValidationError):
            UserCreate(username="carol", email="c@example.com", password="short")

    def test_invalid_email_rejected(self):
        with pytest.raises(ValidationError):
            UserCreate(username="dave", email="not-an-email", password="secret4")


class TestUserUpdate:
    def test_all_fields_optional(self):
        update = UserUpdate()
        assert update.email is None
        assert update.role is None
        assert update.is_active is None

    def test_partial(self):
        update = UserUpdate(is_active=False, full_name="Renamed")
        assert update.is_active is False


class TestLoginRequest:
    def test_both_fields_required(self):
        req = LoginRequest(username="alice", password="secret5")
        assert req.username == "alice"
        with pytest.raises(ValidationError):
            LoginRequest(username="alice")


class TestChangePasswordRequest:
    def test_valid(self):
        req = ChangePasswordRequest(old_password="old1", new_password="newpass1")
        assert req.new_password == "newpass1"

    def test_new_password_min_length(self):
        with pytest.raises(ValidationError):
            ChangePasswordRequest(old_password="old1", new_password="new")


def _user_response() -> UserResponse:
    now = datetime.now(timezone.utc)
    return UserResponse(
        id=1,
        username="alice",
        email="alice@example.com",
        full_name="Alice",
        role=UserRole.ADMIN,
        is_active=True,
        last_login_at=None,
        created_at=now,
        updated_at=now,
    )


class TestUserResponse:
    def test_shape(self):
        resp = _user_response()
        assert resp.role == UserRole.ADMIN
        assert resp.last_login_at is None


class TestLoginResponse:
    def test_token_type_defaults_bearer(self):
        resp = LoginResponse(access_token="jwt-value", user=_user_response())
        assert resp.token_type == "bearer"
        assert resp.user.username == "alice"


class TestTokenPayload:
    def test_fields(self):
        payload = TokenPayload(sub="1", username="alice", role="admin", exp=1900000000)
        assert payload.sub == "1"
        assert payload.exp == 1900000000
