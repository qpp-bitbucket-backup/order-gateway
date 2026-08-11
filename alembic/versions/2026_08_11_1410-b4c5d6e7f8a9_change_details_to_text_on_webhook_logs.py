"""change_details_to_text_on_webhook_logs

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-08-11 14:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b4c5d6e7f8a9'
down_revision: Union[str, Sequence[str], None] = 'a3b4c5d6e7f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Switch details from a strict JSON column to TEXT.

    A JSON column can't hold a bare/unquoted string, so a plain error
    message like ``Order not found`` was stored (and read back) as the
    literal JSON string ``"Order not found"``, quotes included. TEXT lets
    the app decide serialization: plain error strings are stored as-is,
    structured responses are ``json.dumps()``-ed by the caller.
    """
    # Alter the column type first — while it's still JSON-typed, MySQL
    # rejects writing the unquoted result of JSON_UNQUOTE() back into it,
    # since a bare string isn't valid JSON.
    op.alter_column(
        'webhook_logs',
        'details',
        existing_type=sa.JSON(),
        type_=sa.Text(),
        existing_nullable=True,
    )
    op.execute(
        "UPDATE webhook_logs SET details = JSON_UNQUOTE(details) "
        "WHERE details IS NOT NULL AND JSON_VALID(details) AND JSON_TYPE(details) = 'STRING'"
    )


def downgrade() -> None:
    """Downgrade schema.

    Quote existing plain-text values into valid JSON strings first — an
    ALTER to JSON type validates existing data at alter time.
    """
    op.execute(
        "UPDATE webhook_logs SET details = JSON_QUOTE(details) "
        "WHERE details IS NOT NULL"
    )
    op.alter_column(
        'webhook_logs',
        'details',
        existing_type=sa.Text(),
        type_=sa.JSON(),
        existing_nullable=True,
    )
