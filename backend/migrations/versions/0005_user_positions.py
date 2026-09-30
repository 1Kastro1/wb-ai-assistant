"""Team positions and temporary-password rotation."""
from alembic import op
import sqlalchemy as sa

revision = '0005'
down_revision = '0004'


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column['name'] for column in inspector.get_columns('users')}
    if 'position' not in columns:
        op.add_column('users', sa.Column('position', sa.String(), nullable=False, server_default='Менеджер WB'))
    if 'must_change_password' not in columns:
        op.add_column('users', sa.Column('must_change_password', sa.Boolean(), nullable=False, server_default='0'))
    op.execute("UPDATE users SET position='Владелец' WHERE role='owner'")


def downgrade():
    op.drop_column('users', 'must_change_password')
    op.drop_column('users', 'position')
