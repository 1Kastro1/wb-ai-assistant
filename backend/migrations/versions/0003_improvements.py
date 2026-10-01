"""Quality, job queue and store profiles."""
from alembic import op
import sqlalchemy as sa

revision = '0003'
down_revision = '0002'


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if 'store_profiles' not in tables:
        op.create_table(
            'store_profiles',
            sa.Column('id', sa.String(), primary_key=True),
            sa.Column('name', sa.String(), nullable=False),
            sa.Column('created_at', sa.String(), nullable=False),
        )
    draft_columns = {c['name'] for c in inspector.get_columns('generated_answers')}
    if 'quality' not in draft_columns:
        op.add_column('generated_answers', sa.Column('quality', sa.JSON(), nullable=False, server_default='{}'))
    job_columns = {c['name'] for c in inspector.get_columns('jobs')}
    for name, column in (
        ('priority', sa.Column('priority', sa.Integer(), nullable=False, server_default='100')),
        ('progress', sa.Column('progress', sa.Integer(), nullable=False, server_default='0')),
        ('cancel_requested', sa.Column('cancel_requested', sa.Boolean(), nullable=False, server_default='0')),
        ('attempts', sa.Column('attempts', sa.Integer(), nullable=False, server_default='0')),
        ('max_attempts', sa.Column('max_attempts', sa.Integer(), nullable=False, server_default='2')),
    ):
        if name not in job_columns:
            op.add_column('jobs', column)
    columns = {item['name'] for item in sa.inspect(bind).get_columns('store_profiles')}
    store = sa.table('store_profiles', sa.column('id', sa.String()), sa.column('name', sa.String()), sa.column('created_at', sa.String()), sa.column('provider', sa.String()), sa.column('config', sa.JSON()))
    if not bind.execute(sa.select(store.c.id).where(store.c.id == 'owner')).first():
        values = {'id':'owner', 'name':'Мой магазин', 'created_at':'2026-01-01T00:00:00+00:00'}
        if 'provider' in columns: values['provider'] = 'wb'
        if 'config' in columns: values['config'] = {}
        bind.execute(store.insert().values(**values))
    op.execute("UPDATE jobs SET progress=100 WHERE status='completed'")


def downgrade():
    for name in ('max_attempts', 'attempts', 'cancel_requested', 'progress', 'priority'):
        op.drop_column('jobs', name)
    op.drop_column('generated_answers', 'quality')
    op.drop_table('store_profiles')
