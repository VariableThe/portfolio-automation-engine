"""add_stock_cooldowns

Revision ID: 7f3e1a2b4c5d
Revises: b6d36d4f52bf
Create Date: 2026-07-03 13:00:00.000000
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = '7f3e1a2b4c5d'
down_revision: Union[str, None] = 'b6d36d4f52bf'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('stock_cooldowns',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('symbol', sa.String(length=20), nullable=False),
        sa.Column('sell_order_id', sa.String(length=50), nullable=False),
        sa.Column('cooldown_until', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_stock_cooldowns_symbol'),
        'stock_cooldowns',
        ['symbol'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f('ix_stock_cooldowns_symbol'), table_name='stock_cooldowns'
    )
    op.drop_table('stock_cooldowns')
