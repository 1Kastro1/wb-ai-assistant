from alembic import op
from app.db import Job
revision = '0002'
down_revision = '0001'

def upgrade():
    Job.__table__.create(op.get_bind(), checkfirst=True)

def downgrade():
    Job.__table__.drop(op.get_bind(), checkfirst=True)
