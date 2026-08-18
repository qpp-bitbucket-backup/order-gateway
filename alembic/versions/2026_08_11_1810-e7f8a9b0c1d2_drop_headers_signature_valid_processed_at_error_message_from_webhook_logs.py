"""drop_headers_signature_valid_processed_at_error_message_from_webhook_logs

Revision ID: e7f8a9b0c1d2
Revises: d6e7f8a9b0c1
Create Date: 2026-08-11 18:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'e7f8a9b0c1d2'
down_revision: Union[str, Sequence[str], None] = 'd6e7f8a9b0c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema (contract step — only safe once no code still reads/writes these columns)."""
    op.drop_column('webhook_logs', 'headers')
    op.drop_column('webhook_logs', 'signature_valid')
    op.drop_column('webhook_logs', 'processed_at')
    op.drop_column('webhook_logs', 'error_message')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('webhook_logs', sa.Column('error_message', sa.String(length=512), nullable=True))
    op.add_column('webhook_logs', sa.Column('processed_at', sa.DateTime(), nullable=True))
    op.add_column('webhook_logs', sa.Column('signature_valid', sa.Boolean(), nullable=True))
    op.add_column('webhook_logs', sa.Column('headers', sa.JSON(), nullable=True))
