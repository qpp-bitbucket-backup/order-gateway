"""add default_recipient_email to clients

Revision ID: b3c4d5e6f7a8
Revises: a2b3c4d5e6f7
Create Date: 2026-09-21 10:30:00.000000

OMS address fetches (API-001) do not always return an email for the
delivery/billing address. ``clients.default_recipient_email`` stores a
per-client fallback: when OMS returns no email for an address, the
configured default is stored on the Address instead. NULL = no fallback
(existing clients keep today's behaviour).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b3c4d5e6f7a8'
down_revision: Union[str, Sequence[str], None] = 'a2b3c4d5e6f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'clients',
        sa.Column(
            'default_recipient_email',
            sa.String(length=255),
            nullable=True,
            comment=(
                'Fallback email for delivery/billing addresses when the OMS '
                'address fetch returns no email (NULL = no fallback)'
            ),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('clients', 'default_recipient_email')
