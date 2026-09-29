"""rename cooling_off to coolingoff in orderstatus enum

Revision ID: e2f3a4b5c6d7
Revises: d1e2f3a4b5c6
Create Date: 2026-09-23 13:30:00.000000

Unify the status spelling: every OrderStatus value uses the no-underscore
style already established by ``printready`` (and ``returncompleted`` from
the previous revision), so ``cooling_off`` becomes ``coolingoff``.

``cooling_off`` has been live since c9d0e1f2a3b4 (2026-09-10), so this
migration also rewrites existing rows. MySQL would fail (strict mode) or
blank out rows whose value disappears from the ENUM, hence the three-step
dance below: widen -> rewrite data -> narrow.
"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'e2f3a4b5c6d7'
down_revision: Union[str, Sequence[str], None] = 'd1e2f3a4b5c6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Full member list with BOTH spellings during the rewrite (step 1/3).
_BOTH_SPELLINGS = (
    "ENUM('received','pending','validated','cooling_off','coolingoff',"
    "'processing','printready','printed','produced','cancelled','failed',"
    "'errored','shipped','returning','returncompleted') "
    "NOT NULL DEFAULT 'pending'"
)

# Final member list — 'cooling_off' removed (steps 2/3's narrowing and the
# downgrade's starting point).
_ONLY_NEW = (
    "ENUM('received','pending','validated','coolingoff','processing',"
    "'printready','printed','produced','cancelled','failed','errored',"
    "'shipped','returning','returncompleted') "
    "NOT NULL DEFAULT 'pending'"
)


def upgrade() -> None:
    """Rename 'cooling_off' -> 'coolingoff' (data included)."""
    # 1) Accept both spellings so the rewrite below cannot blank out rows.
    op.execute(
        "ALTER TABLE orders MODIFY COLUMN status " + _BOTH_SPELLINGS
    )
    # 2) Rewrite existing rows.
    op.execute("UPDATE orders SET status = 'coolingoff' WHERE status = 'cooling_off'")
    # 3) Drop the old spelling.
    op.execute(
        "ALTER TABLE orders MODIFY COLUMN status " + _ONLY_NEW
    )


def downgrade() -> None:
    """Rename 'coolingoff' back to 'cooling_off' (data included)."""
    # 1) Accept both spellings.
    op.execute(
        "ALTER TABLE orders MODIFY COLUMN status " + _BOTH_SPELLINGS
    )
    # 2) Rewrite existing rows.
    op.execute("UPDATE orders SET status = 'cooling_off' WHERE status = 'coolingoff'")
    # 3) Drop the new spelling.
    op.execute(
        "ALTER TABLE orders MODIFY COLUMN status "
        "ENUM('received','pending','validated','cooling_off','processing',"
        "'printready','printed','produced','cancelled','failed','errored',"
        "'shipped','returning','returncompleted') "
        "NOT NULL DEFAULT 'pending'"
    )
