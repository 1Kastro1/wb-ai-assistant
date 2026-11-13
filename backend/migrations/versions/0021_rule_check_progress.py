"""Persist incomplete archive checks without changing published replies."""
from alembic import op
import sqlalchemy as sa

revision='0021'
down_revision='0020'
branch_labels=None
depends_on=None


def upgrade():
    bind=op.get_bind()
    if 'verification_progress' not in {c['name'] for c in sa.inspect(bind).get_columns('review_library_audit_rows')}:
        op.add_column('review_library_audit_rows',sa.Column('verification_progress',sa.JSON(),nullable=True))


def downgrade():
    op.drop_column('review_library_audit_rows','verification_progress')
