"""make lot and realized_gain source_txn_id nullable

Revision ID: 192e08f1fbf1
Revises: aacd5f1eeceb
Create Date: 2026-08-14 10:58:51.136161

Enables gap-fill lots (compute/service.py): an assumed pre-existing
position inferred from an outflow that exceeds recorded history (e.g. a
Dezerv "BANK BOOK" export whose date window starts mid-holding), which has
no real transaction to point to. SQLite has no native ALTER COLUMN, hence
batch mode (recreate-table-and-copy) rather than a plain op.alter_column,
unlike every other migration in this project so far -- this is the first
one that changes an existing column rather than only adding tables/columns.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '192e08f1fbf1'
down_revision: Union[str, None] = 'aacd5f1eeceb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('lot') as batch_op:
        batch_op.alter_column(
            'source_txn_id', existing_type=sa.String(length=36), nullable=True
        )
    with op.batch_alter_table('realized_gain') as batch_op:
        batch_op.alter_column(
            'source_txn_id', existing_type=sa.String(length=36), nullable=True
        )


def downgrade() -> None:
    with op.batch_alter_table('realized_gain') as batch_op:
        batch_op.alter_column(
            'source_txn_id', existing_type=sa.String(length=36), nullable=False
        )
    with op.batch_alter_table('lot') as batch_op:
        batch_op.alter_column(
            'source_txn_id', existing_type=sa.String(length=36), nullable=False
        )
