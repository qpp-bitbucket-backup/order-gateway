from sqlmodel import Field, Column
from sqlalchemy import JSON
from typing import Optional, Dict, Any, List

from app.models.base import BaseModel


class Client(BaseModel, table=True):
    """API client credentials for OneFlow authentication."""

    __tablename__ = "clients"

    name: str = Field(index=True, nullable=False, description="Client display name")
    store_id: str = Field(unique=True, index=True, nullable=False, description="Associated store ID")
    store_key: Optional[str] = Field(None, index=True, description="Store key for additional identification")
    token: str = Field(unique=True, index=True, nullable=False, description="OneFlow API token")
    secret: str = Field(nullable=False, description="OneFlow API secret for HMAC signing")
    oms_token: Optional[str] = Field(
        None,
        unique=True,
        index=True,
        description=(
            "OMS API token for the OMS cancel/return endpoints "
            "(NULL = OMS auth not configured for this client)"
        ),
    )
    oms_secret: Optional[str] = Field(
        None,
        description=(
            "OMS API secret for HMAC signing of the OMS cancel/return endpoints "
            "(NULL = OMS auth not configured for this client)"
        ),
    )
    description: Optional[str] = Field(default=None, description="Optional client description")
    default_recipient_email: Optional[str] = Field(
        None,
        max_length=255,
        description=(
            "Fallback email for delivery/billing addresses: used when the OMS "
            "address fetch returns no email for the order (NULL = no fallback)"
        ),
    )
    cooling_off_seconds: int = Field(
        default=0,
        nullable=False,
        description=(
            "Order cooling-off period in seconds: a validated order waits in "
            "COOLING_OFF for this long before being pushed to QPMN (0 = no cooling-off)"
        ),
    )
    rate_limit_rps: Optional[int] = Field(
        None,
        description=(
            "Per-client API rate limit in requests/second, enforced after "
            "successful authentication on the signed inbound endpoints "
            "(NULL = global default CLIENT_RATE_LIMIT_RPS, 0 = unlimited)"
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
    brand_label_lines: Optional[List[str]] = Field(
        None,
        sa_column=Column(JSON),
        description=(
            "Per-client packing label template: one string per printed line, "
            "with {sku} and {product_name} placeholders substituted per line "
            "item (e.g. \"产品货号: {sku}\"); NULL / empty = client has not "
            "configured one (label generation skipped — no fallback template exists)"
        ),
    )
    enable_brand_label: bool = Field(
        default=False,
        nullable=False,
        description=(
            "Per-client switch for packing document generation at order "
            "validation (label PDFs + packing slip); False = skip entirely"
        ),
    )
