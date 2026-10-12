"""Single-use invitations for colleague self-registration."""
from alembic import op
import sqlalchemy as sa

revision = '0008'
down_revision = '0007'


def upgrade():
    if 'team_invites' not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            'team_invites',
            sa.Column('token_hash', sa.String(), primary_key=True),
            sa.Column('position', sa.String(), nullable=False),
            sa.Column('store_ids', sa.JSON(), nullable=False),
            sa.Column('created_by', sa.String(), nullable=False),
            sa.Column('created_at', sa.String(), nullable=False),
            sa.Column('expires', sa.Integer(), nullable=False),
            sa.Column('used_by', sa.String(), nullable=False, server_default=''),
        )


def downgrade():
    op.drop_table('team_invites')
