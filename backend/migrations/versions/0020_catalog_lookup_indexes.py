"""Index direct article lookup and the separate legacy-article fallback."""
from alembic import op
import sqlalchemy as sa
from app.catalog_lookup_sql import brand_lookup_key,article_lookup_key

revision='0020'
down_revision='0019'
branch_labels=None
depends_on=None


def upgrade():
    bind=op.get_bind()
    pg=bind.dialect.name=='postgresql'
    # SQLite reflection omits expression indexes created by Base.metadata.
    if bind.dialect.name == 'sqlite':
        known = set(bind.execute(sa.text("SELECT name FROM sqlite_master WHERE type = 'index' AND tbl_name = 'products'")).scalars())
    else:
        known = {index['name'] for index in sa.inspect(bind).get_indexes('products')}
    brand=brand_lookup_key(sa.column('brand'))
    article=article_lookup_key(sa.column('part_number'))
    definitions=[('ix_products_brand_article_lookup',[brand,article],{}),
        ('ix_products_legacy_brand_lookup',[brand],{'postgresql_where':sa.text("part_number = ''"),'sqlite_where':sa.text("part_number = ''")})]
    for name,columns,options in definitions:
        if name in known:continue
        if pg:
            with op.get_context().autocommit_block():
                op.create_index(name,'products',columns,postgresql_concurrently=True,**options)
        else:op.create_index(name,'products',columns,**options)


def downgrade():
    for name in ('ix_products_legacy_brand_lookup','ix_products_brand_article_lookup'):
        op.drop_index(name,table_name='products')
