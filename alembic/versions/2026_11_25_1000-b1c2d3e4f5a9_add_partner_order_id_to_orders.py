"""add partner_order_id to orders

Revision ID: b1c2d3e4f5a9
Revises: e1f2a3b4c5d6
Create Date: 2026-11-25 10:00:00.000000

Adds a nullable ``partner_order_id`` VARCHAR(255) column to ``orders``.
Populated by the platform order-query endpoint
(POST /api/platform/orders/{order_id}/query) from the QPMN store order
detail's ``data.logisticsInfo.orderNumber`` when present.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a9'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'orders',
        sa.Column(
            'partner_order_id',
            sa.String(length=255),
            nullable=True,
            comment=(
                "QPMN partner order number (data.logisticsInfo.orderNumber "
                "from the store order detail)"
            ),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('orders', 'partner_order_id')
