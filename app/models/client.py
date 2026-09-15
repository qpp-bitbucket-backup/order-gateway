from sqlmodel import Field, Column
from sqlalchemy import JSON
from typing import Optional, Dict, Any

from app.models.base import BaseModel


class Client(BaseModel, table=True):
    """API client credentials for OneFlow authentication."""

    __tablename__ = "clients"

    name: str = Field(index=True, nullable=False, description="Client display name")
    store_id: str = Field(unique=True, index=True, nullable=False, description="Associated store ID")
    store_key: Optional[str] = Field(None, index=True, description="Store key for additional identification")
    token: str = Field(unique=True, index=True, nullable=False, description="OneFlow API token")
    secret: str = Field(nullable=False, description="OneFlow API secret for HMAC signing")
    description: Optional[str] = Field(default=None, description="Optional client description")
    cooling_off_seconds: int = Field(
        default=0,
        nullable=False,
        description=(
            "Order cooling-off period in seconds: a validated order waits in "
            "COOLING_OFF for this long before being pushed to QPMN (0 = no cooling-off)"
        ),
    )
    notification_config: Optional[Dict[str, Any]] = Field(
        None,
        sa_column=Column(JSON),
        description=(
            'Per-level order status email notification settings: '
            '{"info"|"warning"|"error": {"enabled": bool, "emails": [...]}}; '
            "NULL = all levels disabled (no status-change emails sent)"
        ),
    )
