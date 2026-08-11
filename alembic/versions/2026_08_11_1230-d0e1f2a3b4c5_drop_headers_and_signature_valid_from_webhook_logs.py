"""drop_headers_and_signature_valid_from_webhook_logs

Revision ID: d0e1f2a3b4c5
Revises: c9d0e1f2a3b4
Create Date: 2026-08-11 12:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd0e1f2a3b4c5'
down_revision: Union[str, Sequence[str], None] = 'c9d0e1f2a3b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_column('webhook_logs', 'headers')
    op.drop_column('webhook_logs', 'signature_valid')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('webhook_logs', sa.Column('signature_valid', sa.Boolean(), nullable=True))
    op.add_column('webhook_logs', sa.Column('headers', sa.JSON(), nullable=True))
