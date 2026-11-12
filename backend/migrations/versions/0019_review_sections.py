"""Preserve separate customer review fields supplied by each marketplace."""
from alembic import op
import sqlalchemy as sa

revision = '0019'
down_revision = '0018'
branch_labels = None
depends_on = None


def upgrade():
    columns = {c['name'] for c in sa.inspect(op.get_bind()).get_columns('reviews')}
    if 'sections' not in columns:
        op.add_column('reviews', sa.Column('sections', sa.JSON(), nullable=False, server_default='{}'))


def downgrade():
    op.drop_column('reviews', 'sections')
