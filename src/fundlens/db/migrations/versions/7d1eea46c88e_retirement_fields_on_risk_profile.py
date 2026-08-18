"""retirement fields on risk_profile

Revision ID: 7d1eea46c88e
Revises: 3b9f640de1ae
Create Date: 2026-08-18 23:03:55.334611

Adds the retirement-projection inputs (compute/retirement.py,
compute-engine.md §16) to the existing RiskProfile singleton. All columns
are nullable -- the projection reports
reason="retirement_profile_incomplete" rather than guessing, so an
existing profile stays valid until the user fills the retirement form.

Autogenerate also proposed alter_column on `nav_history.source` and
`data_freshness.source` (VARCHAR(11) -> Enum). Deliberately dropped: that
difference is cosmetic (SQLite stores both identically and doesn't enforce
the CHECK either way, and the ORM already reads/writes them fine), while
applying it would rewrite `nav_history` -- 139k+ rows on a real portfolio
-- for no functional gain. Not this migration's job.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '7d1eea46c88e'
down_revision: Union[str, None] = '3b9f640de1ae'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('risk_profile', sa.Column('current_age', sa.Integer(), nullable=True))
    op.add_column('risk_profile', sa.Column('retirement_age', sa.Integer(), nullable=True))
    op.add_column('risk_profile', sa.Column('life_expectancy', sa.Integer(), nullable=True))
    op.add_column(
        'risk_profile', sa.Column('annual_investment_paise', sa.Integer(), nullable=True)
    )
    op.add_column(
        'risk_profile',
        sa.Column('annual_investment_step_up_pct', sa.Numeric(precision=5, scale=2), nullable=True),
    )
    op.add_column(
        'risk_profile', sa.Column('monthly_withdrawal_today_paise', sa.Integer(), nullable=True)
    )
    op.add_column(
        'risk_profile',
        sa.Column('post_retirement_return_pct', sa.Numeric(precision=5, scale=2), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('risk_profile', 'post_retirement_return_pct')
    op.drop_column('risk_profile', 'monthly_withdrawal_today_paise')
    op.drop_column('risk_profile', 'annual_investment_step_up_pct')
    op.drop_column('risk_profile', 'annual_investment_paise')
    op.drop_column('risk_profile', 'life_expectancy')
    op.drop_column('risk_profile', 'retirement_age')
    op.drop_column('risk_profile', 'current_age')
