"""goal include_in_retirement_projection

Revision ID: 8a41c02b7f13
Revises: 67373d8ea0e9
Create Date: 2026-08-19

Lets a goal opt out of being drawn from the corpus as a life event in the
retirement projection (compute-engine.md §17). Defaults to true via
server_default, so every existing goal keeps the behaviour it already had
without a data migration.

The case this exists for: a goal literally tracking "retirement" duplicates
the projection's own income stream, so counting it as a lump-sum spend as
well would double-count. Also covers goals funded from outside the
portfolio.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '8a41c02b7f13'
down_revision: Union[str, None] = '67373d8ea0e9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'goal',
        sa.Column(
            'include_in_retirement_projection',
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )


def downgrade() -> None:
    op.drop_column('goal', 'include_in_retirement_projection')
