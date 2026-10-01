"""Team permissions, marketplaces, notifications and audit metadata."""
from alembic import op
import sqlalchemy as sa

revision = '0007'
down_revision = '0006'


def add(table, column):
    names = {item['name'] for item in sa.inspect(op.get_bind()).get_columns(table)}
    if column.name not in names:
        op.add_column(table, column)


def upgrade():
    add('store_profiles', sa.Column('provider', sa.String(), nullable=False, server_default='wb'))
    add('store_profiles', sa.Column('config', sa.JSON(), nullable=False, server_default='{}'))
    add('users', sa.Column('permissions', sa.JSON(), nullable=False, server_default='[]'))
    add('users', sa.Column('store_ids', sa.JSON(), nullable=False, server_default='[]'))
    add('user_activity', sa.Column('entity_type', sa.String(), nullable=False, server_default=''))
    add('user_activity', sa.Column('entity_id', sa.String(), nullable=False, server_default=''))
    add('user_activity', sa.Column('details', sa.JSON(), nullable=False, server_default='{}'))
    add('products', sa.Column('marketplace', sa.String(), nullable=False, server_default='wb'))
    add('reviews', sa.Column('marketplace', sa.String(), nullable=False, server_default='wb'))
    add('generated_answers', sa.Column('created_by', sa.String(), nullable=False, server_default=''))
    add('generated_answers', sa.Column('updated_by', sa.String(), nullable=False, server_default=''))
    if 'notifications' not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table('notifications',
            sa.Column('id', sa.String(), primary_key=True), sa.Column('kind', sa.String(), nullable=False),
            sa.Column('title', sa.String(), nullable=False), sa.Column('message', sa.Text(), nullable=False),
            sa.Column('severity', sa.String(), nullable=False, server_default='info'),
            sa.Column('user_id', sa.String(), nullable=False, server_default=''),
            sa.Column('store_id', sa.String(), nullable=False, server_default=''),
            sa.Column('read', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('telegram_sent', sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column('created_at', sa.String(), nullable=False))


def downgrade():
    op.drop_table('notifications')
    for table, names in (
        ('generated_answers', ('updated_by','created_by')), ('reviews', ('marketplace',)),
        ('products', ('marketplace',)), ('user_activity', ('details','entity_id','entity_type')),
        ('users', ('store_ids','permissions')), ('store_profiles', ('config','provider'))):
        for name in names:
            op.drop_column(table, name)
