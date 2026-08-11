"""rename_error_message_to_details_on_webhook_logs

Revision ID: f2a3b4c5d6e7
Revises: e1f2a3b4c5d6
Create Date: 2026-08-11 13:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'f2a3b4c5d6e7'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema.

    ``error_message`` was a plain VARCHAR; existing values aren't valid JSON
    literals (no surrounding quotes), so a straight type-changing rename
    would fail MySQL's JSON validation on the 8 existing non-null rows.
    Adds the new column, backfills via JSON_QUOTE, then drops the old one.
    """
    op.add_column('webhook_logs', sa.Column('details', sa.JSON(), nullable=True))
    op.execute(
        "UPDATE webhook_logs SET details = JSON_QUOTE(error_message) "
        "WHERE error_message IS NOT NULL"
    )
    op.drop_column('webhook_logs', 'error_message')


def downgrade() -> None:
    """Downgrade schema."""
    op.add_column('webhook_logs', sa.Column('error_message', sa.String(length=512), nullable=True))
    op.execute(
        "UPDATE webhook_logs SET error_message = JSON_UNQUOTE(details) "
        "WHERE details IS NOT NULL"
    )
    op.drop_column('webhook_logs', 'details')
