"""Durable categories for full-review refresh checkpoints."""
from alembic import op
import sqlalchemy as sa

revision = '0017'
down_revision = '0016'
branch_labels = None
depends_on = None


def upgrade():
    if 'category' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('generation_outcomes')}:
        op.add_column('generation_outcomes', sa.Column('category', sa.String(), nullable=False, server_default='generated'))


def downgrade():
    op.drop_column('generation_outcomes', 'category')
