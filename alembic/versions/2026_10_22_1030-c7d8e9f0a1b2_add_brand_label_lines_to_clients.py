"""add brand_label_lines and enable_brand_label to clients

Revision ID: c7d8e9f0a1b2
Revises: b6c7d8e9f0a1
Create Date: 2026-10-22 10:30:00.000000

Per-client packing label configuration, replacing the global
``BRAND_LABEL_ENABLED`` env var:

- ``enable_brand_label`` (bool, NOT NULL, server default false): per-client
  switch for packing document generation at order validation. Existing rows
  default to false (feature stays off until explicitly enabled per client).
- ``brand_label_lines`` (JSON, nullable): per-client label template — one
  string per printed line with ``{sku}`` / ``{product_name}`` placeholders,
  substituted per line item when the packing label PDF is generated
  (``packing.build_label_pdf``). NULL = built-in default template
  (``packing._label_lines``); the update endpoints normalise an empty array
  back to NULL so "reset to default" is expressible over the API.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c7d8e9f0a1b2'
down_revision: Union[str, Sequence[str], None] = 'b6c7d8e9f0a1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        'clients',
        sa.Column(
            'brand_label_lines',
            sa.JSON(),
            nullable=True,
            comment=(
                'Per-client packing label template: one string per printed '
                'line with {sku}/{product_name} placeholders '
                '(NULL = built-in default template)'
            ),
        ),
    )
    op.add_column(
        'clients',
        sa.Column(
            'enable_brand_label',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
            comment=(
                'Per-client switch for packing document generation at order '
                'validation (label PDFs + packing slip)'
            ),
        ),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('clients', 'enable_brand_label')
    op.drop_column('clients', 'brand_label_lines')
