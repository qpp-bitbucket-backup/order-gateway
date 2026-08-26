"""add creation_payload to orders

Revision ID: a1b2c3d4e6f7
Revises: f9a0b1c2d3e5
Create Date: 2026-08-26 10:00:00.000000

Adds a nullable ``creation_payload`` JSON column to ``orders`` storing the
QPMN create-order payload submitted by the last push attempt. The field is
internal only and is never exposed in any API response.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e6f7'
down_revision: Union[str, Sequence[str], None] = 'f9a0b1c2d3e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'orders',
        sa.Column('creation_payload', sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('orders', 'creation_payload')
