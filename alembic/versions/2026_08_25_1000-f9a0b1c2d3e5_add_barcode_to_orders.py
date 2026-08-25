"""add barcode to orders

Revision ID: f9a0b1c2d3e5
Revises: e8a9b0c1d2e4
Create Date: 2026-08-25 10:00:00.000000

Adds a nullable ``barcode`` VARCHAR(32) column to ``orders``. The field is
internal only and is never exposed in any API response.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f9a0b1c2d3e5'
down_revision: Union[str, Sequence[str], None] = 'e8a9b0c1d2e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'orders',
        sa.Column('barcode', sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('orders', 'barcode')
