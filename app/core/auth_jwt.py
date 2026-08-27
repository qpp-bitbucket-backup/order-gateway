"""JWT authentication dependencies for FastAPI."""
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlmodel import Session, select

from app.core.database import engine
from app.core.security import decode_access_token
from app.models.user import User, UserRole

logger = logging.getLogger(__name__)

# Bearer token scheme
bearer_scheme = HTTPBearer(auto_error=False)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(bearer_scheme),
) -> User:
    """
    Extract and validate the current user from JWT Bearer token.

    Raises:
        HTTPException 401 if token is missing, invalid, or user not found.
        HTTPException 403 if user is inactive.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authorization token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = credentials.credentials
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    with Session(engine) as session:
        user = session.exec(select(User).where(User.id == int(user_id))).first()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )

    return user


def require_role(*roles: UserRole):
    """
    Dependency factory: require the current user to have one of the specified roles.

    Usage:
        @router.get("/admin-only")
        def admin_endpoint(user: User = Depends(require_role(UserRole.ADMIN))):
            ...
    """
    def _check_role(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user.role.value}' is not authorized. Required: {[r.value for r in roles]}",
            )
        return user
    return _check_role


def require_admin(user: User = Depends(get_current_user)) -> User:
    """Shortcut dependency: require admin role."""
    if user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


def require_editor_or_above(user: User = Depends(get_current_user)) -> User:
    """Shortcut dependency: require at least editor role."""
    if user.role not in (UserRole.ADMIN, UserRole.EDITOR):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Editor or admin access required",
        )
    return user


def resolve_scoped_store_id(
    user: User,
    requested_store_id: Optional[str] = None,
) -> Optional[str]:
    """
    Resolve the store scope for platform data access, by role:

    - ADMIN: unrestricted — the optional ``requested_store_id`` filter
      (None = all stores) is honored as-is, regardless of the admin's own
      ``store_id``.
    - EDITOR / VIEWER: always scoped to ``user.store_id``. A user bound to
      no store sees nothing (403), and requests targeting a different
      store are rejected (403) instead of silently falling back.

    Returns the store ID the query must be filtered by (None = no filter,
    admin only).
    """
    if user.role == UserRole.ADMIN:
        return requested_store_id
    if not user.store_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User is not associated with any store",
        )
    if requested_store_id and requested_store_id != user.store_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Access denied to store '{requested_store_id}'",
        )
    return user.store_id
