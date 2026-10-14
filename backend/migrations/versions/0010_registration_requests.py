"""Registration requires the designated owner's approval."""
from alembic import op
import sqlalchemy as sa

revision = '0010'
down_revision = '0009'


def upgrade():
    op.create_table('registration_requests',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('username', sa.String(), nullable=False),
        sa.Column('password_hash', sa.Text(), nullable=False),
        sa.Column('receipt_hash', sa.String(), nullable=False, unique=True),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('created_at', sa.String(), nullable=False),
        sa.Column('reviewed_at', sa.String(), nullable=False),
        sa.Column('reviewed_by', sa.String(), nullable=False))
    op.create_index('ix_registration_requests_username', 'registration_requests', ['username'], unique=True)


def downgrade():
    op.drop_table('registration_requests')
