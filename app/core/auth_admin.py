from typing import Optional

from fastapi import Header, HTTPException, status

from app.core.config import settings


def verify_admin_key(x_admin_key: Optional[str] = Header(None, alias="x-admin-key")) -> str:
    """Verify admin API key for client management endpoints."""
    if not x_admin_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="x-admin-key header is required",
            headers={"X-Error-Code": "MISSING_ADMIN_KEY"},
        )

    if x_admin_key != settings.ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid admin key",
            headers={"X-Error-Code": "INVALID_ADMIN_KEY"},
        )

    return x_admin_key
