"""Audit successful team mutations."""
from alembic import op
import sqlalchemy as sa

revision = '0006'
down_revision = '0005'


def upgrade():
    if 'user_activity' not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            'user_activity',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('timestamp', sa.String(), nullable=False),
            sa.Column('user_id', sa.String(), nullable=False),
            sa.Column('username', sa.String(), nullable=False),
            sa.Column('method', sa.String(), nullable=False),
            sa.Column('path', sa.String(), nullable=False),
            sa.Column('status', sa.Integer(), nullable=False),
        )


def downgrade():
    op.drop_table('user_activity')
