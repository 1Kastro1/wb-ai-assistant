"""Separate team accounts and persistent sessions."""
from alembic import op
import sqlalchemy as sa

revision = '0004'
down_revision = '0003'


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if 'users' not in tables:
        op.create_table(
            'users',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('username', sa.String(), nullable=False),
            sa.Column('display_name', sa.String(), nullable=False),
            sa.Column('password_hash', sa.Text(), nullable=False),
            sa.Column('role', sa.String(), nullable=False, server_default='member'),
            sa.Column('enabled', sa.Boolean(), nullable=False, server_default='1'),
            sa.Column('created_at', sa.String(), nullable=False),
            sa.UniqueConstraint('username'),
        )
        op.create_index('ix_users_username', 'users', ['username'], unique=True)
    session_columns = {c['name'] for c in inspector.get_columns('sessions')}
    if 'user_id' not in session_columns:
        op.add_column('sessions', sa.Column('user_id', sa.String(), nullable=False, server_default='owner'))


def downgrade():
    op.drop_column('sessions', 'user_id')
    op.drop_index('ix_users_username', table_name='users')
    op.drop_table('users')
