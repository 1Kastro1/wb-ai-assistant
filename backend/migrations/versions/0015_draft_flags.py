"""Project quality flags once per write, instead of detoasting every dashboard count."""
from alembic import op
from app.draft_flags import flag_sql
import sqlalchemy as sa

revision = '0015'
down_revision = '0014'
branch_labels = None
depends_on = None


def upgrade():
    dialect = op.get_bind().dialect.name
    if 'created_at' not in {c['name'] for c in sa.inspect(op.get_bind()).get_columns('generation_outcomes')}:
        op.add_column('generation_outcomes', sa.Column('created_at', sa.String(), nullable=False, server_default=''))
        op.create_index('ix_generation_outcomes_created_at', 'generation_outcomes', ['created_at'])
    existing = {column['name'] for column in sa.inspect(op.get_bind()).get_columns('generated_answers')}
    definitions = [f"ADD COLUMN quality_{name} BOOLEAN GENERATED ALWAYS AS ({flag_sql(name, dialect)}) " + ('STORED' if dialect == 'postgresql' else 'VIRTUAL') for name in ('passed', 'technical', 'pending', 'attention') if 'quality_' + name not in existing]
    if not definitions:
        return
    if dialect == 'postgresql':
        op.execute('ALTER TABLE generated_answers ' + ', '.join(definitions))
    else:
        for definition in definitions:
            op.execute('ALTER TABLE generated_answers ' + definition)


def downgrade():
    op.drop_index('ix_generation_outcomes_created_at', 'generation_outcomes')
    op.drop_column('generation_outcomes', 'created_at')
    for name in ('attention', 'pending', 'technical', 'passed'):
        op.drop_column('generated_answers', 'quality_' + name)
