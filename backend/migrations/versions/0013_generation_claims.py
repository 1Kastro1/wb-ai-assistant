"""Expiring per-review generation claims shared by API and worker."""
from alembic import op
import sqlalchemy as sa

revision = '0013'
down_revision = '0012'


def upgrade():
    if not sa.inspect(op.get_bind()).has_table('generation_claims'):
        op.create_table('generation_claims',
                        sa.Column('review_id', sa.String(), primary_key=True),
                        sa.Column('token', sa.String(), nullable=False),
                        sa.Column('expires_at', sa.Float(), nullable=False))
    if not sa.inspect(op.get_bind()).has_table('generation_outcomes'):
        op.create_table('generation_outcomes',
                        sa.Column('job_id', sa.String(), primary_key=True),
                        sa.Column('review_id', sa.String(), primary_key=True),
                        sa.Column('attention', sa.Boolean(), nullable=False))


def downgrade():
    op.drop_table('generation_outcomes')
    op.drop_table('generation_claims')
