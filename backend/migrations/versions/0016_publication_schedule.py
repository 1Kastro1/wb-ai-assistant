"""Index durable publication outcomes by their scheduled job."""
from alembic import op
import sqlalchemy as sa
revision = '0016'
down_revision = '0015'


def upgrade():
    bind = op.get_bind()
    if 'scheduled_job_id' not in {c['name'] for c in sa.inspect(bind).get_columns('publication_jobs')}:
        op.add_column('publication_jobs', sa.Column('scheduled_job_id', sa.String(), nullable=False, server_default=''))
    if 'ix_publication_jobs_scheduled_job_id' not in {i['name'] for i in sa.inspect(bind).get_indexes('publication_jobs')}:
        op.create_index('ix_publication_jobs_scheduled_job_id', 'publication_jobs', ['scheduled_job_id'])


def downgrade():
    op.drop_index('ix_publication_jobs_scheduled_job_id', 'publication_jobs')
    op.drop_column('publication_jobs', 'scheduled_job_id')
