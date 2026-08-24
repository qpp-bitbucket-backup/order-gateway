"""add_product_design_data_to_skus

Revision ID: c2d3e4f5a7b8
Revises: b1c2d3e4f5a7
Create Date: 2026-08-24 11:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c2d3e4f5a7b8'
down_revision: Union[str, Sequence[str], None] = 'b1c2d3e4f5a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('skus', sa.Column('product_design_data', sa.JSON(), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('skus', 'product_design_data')
