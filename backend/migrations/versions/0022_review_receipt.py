"""Record actual local receipt without inventing historical timestamps."""
from alembic import op
import sqlalchemy as sa

revision = '0022'
down_revision = '0021'
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if 'received_at' not in {c['name'] for c in inspector.get_columns('reviews')}:
        op.add_column('reviews', sa.Column('received_at', sa.String(), nullable=False, server_default=''))
    if 'ix_reviews_received_at' not in {i['name'] for i in inspector.get_indexes('reviews')}:
        op.create_index('ix_reviews_received_at', 'reviews', ['received_at'])
    if 'confirmed_at' not in {c['name'] for c in inspector.get_columns('publication_jobs')}:
        op.add_column('publication_jobs', sa.Column('confirmed_at', sa.String(), nullable=False, server_default=''))
    if 'ix_publication_jobs_confirmed_at' not in {i['name'] for i in inspector.get_indexes('publication_jobs')}:
        op.create_index('ix_publication_jobs_confirmed_at', 'publication_jobs', ['confirmed_at'])
    from sqlalchemy.dialects.postgresql import JSONB
    answers=sa.table('generated_answers',sa.column('quality',sa.JSON().with_variant(JSONB(),'postgresql')))
    expression=str(answers.c.quality['owner_rules']['checked_at'].as_string().compile(
        dialect=op.get_bind().dialect,compile_kwargs={'literal_binds':True,'include_table':False}))
    op.execute(sa.text('CREATE INDEX IF NOT EXISTS ix_answers_rule_checked_at ON generated_answers ('+expression+')'))


def downgrade():
    op.execute(sa.text('DROP INDEX IF EXISTS ix_answers_rule_checked_at'))
    op.drop_index('ix_publication_jobs_confirmed_at', table_name='publication_jobs')
    op.drop_column('publication_jobs', 'confirmed_at')
    op.drop_index('ix_reviews_received_at', table_name='reviews')
    op.drop_column('reviews', 'received_at')
