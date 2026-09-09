"""widen_source_sku_for_regex_patterns

Revision ID: e6f7a8b9c0d1
Revises: c4d5e6f7a9b0
Create Date: 2026-09-09 11:00:00.000000

source_sku now stores a regex pattern (matched with re.fullmatch against
incoming items[].sku); 32 chars is too tight for patterns, widen to 255.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e6f7a8b9c0d1'
down_revision: Union[str, Sequence[str], None] = 'c4d5e6f7a9b0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        'skus',
        'source_sku',
        existing_type=sa.String(length=32),
        type_=sa.String(length=255),
        existing_nullable=True,
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.alter_column(
        'skus',
        'source_sku',
        existing_type=sa.String(length=255),
        type_=sa.String(length=32),
        existing_nullable=True,
    )
