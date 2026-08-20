"""data_freshness rows_considered

Adds how many rows a fetch examined, alongside how many it inserted.
Without it `rows_updated = 0` is ambiguous between "already current",
"nothing held to look up" and "the parser silently returned nothing" --
the ambiguity that let AMFI's Aug-2026 column change go unnoticed while
the refresh reported OK daily.

Autogenerate also proposed VARCHAR -> Enum alterations on
`data_freshness.source` and `nav_history.source`. Those are dropped
deliberately: SQLite stores Enum as VARCHAR, so the "change" is an
artifact of reflection, not real drift. Keeping them would rebuild both
tables (batch mode recreates and copies) for no schema change, against a
database holding real financial history.

Revision ID: f0db35502b4f
Revises: d8c4eb8fb5bd
Create Date: 2026-08-20 21:11:34.861039

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f0db35502b4f"
down_revision: str | None = "d8c4eb8fb5bd"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("data_freshness", sa.Column("rows_considered", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("data_freshness", "rows_considered")
