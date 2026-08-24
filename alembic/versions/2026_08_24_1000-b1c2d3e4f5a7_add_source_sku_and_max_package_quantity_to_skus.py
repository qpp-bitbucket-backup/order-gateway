"""add_source_sku_and_max_package_quantity_to_skus

Revision ID: b1c2d3e4f5a7
Revises: f8a9b0c1d2e3
Create Date: 2026-08-24 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b1c2d3e4f5a7'
down_revision: Union[str, Sequence[str], None] = 'f8a9b0c1d2e3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('skus', sa.Column('source_sku', sa.String(length=32), nullable=True))
    op.add_column(
        'skus',
        sa.Column('max_package_quantity', sa.Integer(), nullable=False, server_default='50'),
    )
    op.create_index(op.f('ix_skus_source_sku'), 'skus', ['source_sku'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_skus_source_sku'), table_name='skus')
    op.drop_column('skus', 'max_package_quantity')
    op.drop_column('skus', 'source_sku')
