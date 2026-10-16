"""add oms_token and oms_secret to clients

Revision ID: f3a4b5c6d7e8
Revises: e2f3a4b5c6d7
Create Date: 2026-09-23 15:00:00.000000

Per-client OMS credentials for the OMS cancel/return endpoints. Replaces
the former global ``OMS_API_KEY`` / ``OMS_API_SECRET`` settings — the
inbound OMS auth (app/core/auth_oms.py) now looks the token up in the
``clients`` table and verifies the HMAC signature against that client's
``oms_secret``.

Both columns are NULLable: a client without OMS credentials simply cannot
authenticate against the OMS endpoints (403 INVALID_TOKEN). ``oms_token``
gets a UNIQUE index so a presented token resolves to at most one client;
MySQL/SQLite allow multiple NULLs in a unique index.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3a4b5c6d7e8'
down_revision: Union[str, Sequence[str], None] = 'e2f3a4b5c6d7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add per-client OMS credential columns."""
    op.add_column('clients', sa.Column('oms_token', sa.String(length=255), nullable=True))
    op.add_column('clients', sa.Column('oms_secret', sa.String(length=255), nullable=True))
    op.create_index('ix_clients_oms_token', 'clients', ['oms_token'], unique=True)


def downgrade() -> None:
    """Drop per-client OMS credential columns."""
    op.drop_index('ix_clients_oms_token', table_name='clients')
    op.drop_column('clients', 'oms_secret')
    op.drop_column('clients', 'oms_token')
