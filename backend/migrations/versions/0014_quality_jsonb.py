"""Avoid reparsing full quality reports for every dashboard predicate."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0014'
down_revision = '0013'


def upgrade():
    connection = op.get_bind()
    if connection.dialect.name != 'postgresql':
        return
    column = next(c for c in sa.inspect(connection).get_columns('generated_answers') if c['name'] == 'quality')
    if not isinstance(column['type'], JSONB):
        op.alter_column('generated_answers', 'quality', type_=JSONB(),
                        postgresql_using='quality::jsonb', existing_nullable=False)


def downgrade():
    if op.get_bind().dialect.name == 'postgresql':
        op.alter_column('generated_answers', 'quality', type_=sa.JSON(),
                        postgresql_using='quality::json', existing_nullable=False)
