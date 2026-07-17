"""User schemas for request/response validation."""
from typing import Optional
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field
from app.models.user import UserRole


# ── Request Schemas ──────────────────────────────────────────────

class UserCreate(BaseModel):
    """Schema for creating a new user."""
    username: str = Field(..., min_length=3, max_length=100, description="Login username")
    email: EmailStr = Field(..., description="User email address")
    password: str = Field(..., min_length=6, max_length=128, description="Plain password (will be hashed)")
    full_name: Optional[str] = Field(None, max_length=200, description="User full name")
    role: UserRole = Field(default=UserRole.VIEWER, description="User role")
    store_id: Optional[str] = Field(None, description="Associated store ID")


class UserUpdate(BaseModel):
    """Schema for updating a user (all fields optional)."""
    email: Optional[EmailStr] = None
    password: Optional[str] = Field(None, min_length=6, max_length=128)
    full_name: Optional[str] = Field(None, max_length=200)
    role: Optional[UserRole] = None
    store_id: Optional[str] = None
    is_active: Optional[bool] = None


class LoginRequest(BaseModel):
    """Schema for login request."""
    username: str = Field(..., description="Username or email")
    password: str = Field(..., description="Plain password")


class ChangePasswordRequest(BaseModel):
    """Schema for changing password."""
    old_password: str = Field(..., description="Current password")
    new_password: str = Field(..., min_length=6, max_length=128, description="New password")


# ── Response Schemas ─────────────────────────────────────────────

class UserResponse(BaseModel):
    """Schema for user response (excludes hashed_password)."""
    id: int
    username: str
    email: str
    full_name: Optional[str] = None
    role: UserRole
    store_id: Optional[str] = None
    is_active: bool
    last_login_at: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class LoginResponse(BaseModel):
    """Schema for login response."""
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class TokenPayload(BaseModel):
    """Schema for JWT token payload."""
    sub: str  # user_id
    username: str
    role: str
    exp: int
