"""User model for platform authentication."""
from sqlmodel import Field
from typing import Optional
from enum import Enum
from app.models.base import BaseModel


class UserRole(str, Enum):
    """User role enumeration."""
    ADMIN = "admin"
    EDITOR = "editor"
    VIEWER = "viewer"


class User(BaseModel, table=True):
    """User model for platform login and authorization."""

    __tablename__ = "users"

    username: str = Field(unique=True, index=True, nullable=False, max_length=100, description="Login username")
    email: str = Field(unique=True, index=True, nullable=False, max_length=255, description="User email address")
    hashed_password: str = Field(nullable=False, max_length=255, description="Bcrypt hashed password")
    full_name: Optional[str] = Field(None, max_length=200, description="User full name")
    role: UserRole = Field(default=UserRole.VIEWER, nullable=False, description="User role: admin, editor, viewer")
    store_id: Optional[str] = Field(None, index=True, max_length=255, description="Associated store ID (null = all stores)")
    last_login_at: Optional[str] = Field(None, max_length=50, description="Last login timestamp (ISO 8601)")
    failed_login_count: int = Field(default=0, description="Consecutive failed login attempts")
