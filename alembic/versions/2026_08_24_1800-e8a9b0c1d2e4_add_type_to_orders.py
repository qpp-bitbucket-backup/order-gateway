"""add type to orders

Revision ID: e8a9b0c1d2e4
Revises: d3e4f5a7b8c9
Create Date: 2026-08-24 18:00:00.000000

Adds an ``ordertype`` ENUM('base_card', 'parallel_card') column to
``orders`` with server_default 'base_card' so existing rows backfill.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e8a9b0c1d2e4'
down_revision: Union[str, Sequence[str], None] = 'd3e4f5a7b8c9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'orders',
        sa.Column(
            'type',
            sa.Enum('base_card', 'parallel_card', name='ordertype'),
            nullable=False,
            server_default='base_card',
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('orders', 'type')
