"""goal inflation override

Revision ID: 67373d8ea0e9
Revises: 7d1eea46c88e
Create Date: 2026-08-19

Per-goal inflation rate, overriding the global `inflation_pct` assumption
(compute-engine.md §17) -- education and healthcare inflate faster than the
general rate. Nullable: None means "use the global rate", so every existing
goal keeps behaving exactly as before.

Autogenerate again proposed cosmetic VARCHAR -> Enum alter_columns on
`nav_history.source` / `data_freshness.source`; dropped for the same reason
as migration 7d1eea46c88e -- SQLite stores and reads both identically, and
applying it would rewrite a 300k+ row table for no functional gain.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '67373d8ea0e9'
down_revision: Union[str, None] = '7d1eea46c88e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'goal',
        sa.Column('inflation_pct_override', sa.Numeric(precision=5, scale=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('goal', 'inflation_pct_override')
