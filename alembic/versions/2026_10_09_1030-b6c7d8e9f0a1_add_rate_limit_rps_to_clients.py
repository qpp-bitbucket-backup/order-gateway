"""add rate_limit_rps to clients

Revision ID: b6c7d8e9f0a1
Revises: a4b5c6d7e8f9
Create Date: 2026-10-09 10:30:00.000000

Per-client API rate limiting (default 20 req/s):

- Each ``clients`` row can carry its own ``rate_limit_rps`` override,
  enforced after successful authentication inside the signed-header
  auth dependencies (OneFlow and OMS endpoints).
- NULL = fall back to the global default (``CLIENT_RATE_LIMIT_RPS``,
  default 20); 0 = unlimited for that client.
- Exceeding the limit rejects with HTTP 429 + ``Retry-After``.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b6c7d8e9f0a1'
down_revision: Union[str, Sequence[str], None] = 'a4b5c6d7e8f9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'clients',
        sa.Column(
            'rate_limit_rps',
            sa.Integer(),
            nullable=True,
            comment=(
                'Per-client API rate limit in requests/second, enforced after '
                'successful authentication on the signed inbound endpoints '
                '(NULL = global default CLIENT_RATE_LIMIT_RPS, 0 = unlimited)'
            ),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('clients', 'rate_limit_rps')
