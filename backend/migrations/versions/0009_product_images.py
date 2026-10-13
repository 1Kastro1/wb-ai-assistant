"""Store marketplace product thumbnails without changing product facts."""
from alembic import op
import sqlalchemy as sa

revision = '0009'
down_revision = '0008'


def upgrade():
    columns = {item['name'] for item in sa.inspect(op.get_bind()).get_columns('products')}
    if 'image_url' not in columns:
        op.add_column('products', sa.Column('image_url', sa.String(), nullable=False, server_default=''))


def downgrade():
    op.drop_column('products', 'image_url')
