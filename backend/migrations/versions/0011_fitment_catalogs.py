"""Store-scoped cross catalogs and seller listing evidence."""
from alembic import op
import sqlalchemy as sa
revision = '0011'
down_revision = '0010'

def upgrade():
    inspector=sa.inspect(op.get_bind())
    if not inspector.has_table('fitment_imports'):
        op.create_table('fitment_imports', sa.Column('id',sa.String(),primary_key=True),sa.Column('store_id',sa.String(),nullable=False),sa.Column('source',sa.String(),nullable=False),sa.Column('digest',sa.String(),nullable=False),sa.Column('verified',sa.Boolean(),nullable=False),sa.Column('complete',sa.Boolean(),nullable=False),sa.Column('active',sa.Boolean(),nullable=False),sa.Column('rows',sa.JSON(),nullable=False),sa.Column('created_at',sa.String(),nullable=False))
        op.create_index('ix_fitment_imports_store_id','fitment_imports',['store_id'])
    if not inspector.has_table('store_listings'):
        op.create_table('store_listings',sa.Column('store_id',sa.String(),primary_key=True),sa.Column('product_id',sa.String(),primary_key=True),sa.Column('seen_at',sa.String(),nullable=False))

def downgrade():
    op.drop_table('store_listings')
    op.drop_index('ix_fitment_imports_store_id',table_name='fitment_imports')
    op.drop_table('fitment_imports')
