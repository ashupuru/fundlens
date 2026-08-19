"""ai_question table

Revision ID: d8c4eb8fb5bd
Revises: ac8df065f1df
Create Date: 2026-08-20 04:08:43.140554

Autogenerate also proposed widening `data_freshness.source` and
`nav_history.source` from VARCHAR(11) to the NavSource enum. Both were
dropped from this migration deliberately: that drift predates this change,
is unrelated to it, and SQLite implements ALTER COLUMN by rebuilding the
table -- not something to do to real NAV history as a side effect of
adding an unrelated table. It stays visible to the next autogenerate,
which is the right place to decide about it.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd8c4eb8fb5bd'
down_revision: Union[str, None] = 'ac8df065f1df'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('ai_question',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('question', sa.Text(), nullable=False),
    sa.Column('answer_summary', sa.Text(), nullable=False),
    sa.Column('evidence_json', sa.Text(), nullable=False),
    sa.Column('tool_calls_json', sa.Text(), nullable=False),
    sa.Column('accepted_json', sa.Text(), nullable=False),
    sa.Column('rejected_json', sa.Text(), nullable=False),
    sa.Column('model', sa.String(length=50), nullable=False),
    sa.Column('status', sa.Enum('OK', 'UNAVAILABLE', 'ALL_REJECTED', name='aianalysisstatus'), nullable=False),
    sa.Column('error_message', sa.String(length=500), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_ai_question_created_at'), 'ai_question', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_ai_question_created_at'), table_name='ai_question')
    op.drop_table('ai_question')
