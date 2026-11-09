"""add api_version to skus

Revision ID: e1f2a3b4c5d6
Revises: d9e0f1a2b3c4
Create Date: 2026-11-09 14:30:00.000000

Per-SKU QPMN create-order API selection ("open" | "legacy"), replacing the
global ``QPMN_ORDER_API_VERSION`` env var: the push task now resolves the
API from the order's items' SKUs, so one gateway can serve both API shapes
concurrently (per store / per SKU).

Existing rows default to 'open'. Deployment note: if this environment
previously pushed everything through the legacy API
(``QPMN_ORDER_API_VERSION=legacy``), backfill the legacy-facing SKUs right
after migrating, e.g.:

    UPDATE skus SET api_version = 'legacy'
    WHERE customize_project IS NOT NULL AND product_design_data IS NULL;

or flip individual SKUs via ``PUT /api/platform/skus/{sku_id}``
(``{"apiVersion": "legacy"}``).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'd9e0f1a2b3c4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'skus',
        sa.Column(
            'api_version',
            sa.String(length=8),
            nullable=False,
            server_default='open',
            comment=(
                "QPMN create-order API this SKU pushes through: 'open' or "
                "'legacy'; resolved per order from its items' SKUs"
            ),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('skus', 'api_version')
