"""Index store lists and queue selection without locking PostgreSQL writes."""
from alembic import op
import sqlalchemy as sa

revision = '0012'
down_revision = '0011'
INDEXES = (
    ('ix_reviews_store_created_id', 'reviews', ['wb_account_id', 'created_at', 'id']),
    ('ix_reviews_product', 'reviews', ['product_id']),
    ('ix_jobs_queue', 'jobs', ['status', 'priority', 'created_at']),
    ('ix_jobs_kind_created', 'jobs', ['kind', 'created_at']),
)


def upgrade():
    pg = op.get_bind().dialect.name == 'postgresql'
    for name, table, columns in INDEXES:
        if name in {index['name'] for index in sa.inspect(op.get_bind()).get_indexes(table)}:
            continue
        if pg:
            with op.get_context().autocommit_block():
                op.create_index(name, table, columns, postgresql_concurrently=True)
        else:
            op.create_index(name, table, columns)


def downgrade():
    for name, table, _ in reversed(INDEXES):
        op.drop_index(name, table_name=table)
