"""Durable deduplicated replies and resumable sequential review audits."""
from alembic import op
from app.db import VerifiedReplyText, VerifiedReplyContext, ReviewLibraryAuditRow

revision='0018'
down_revision='0017'
branch_labels=None
depends_on=None


def upgrade():
    for model in (VerifiedReplyText,VerifiedReplyContext,ReviewLibraryAuditRow):
        model.__table__.create(op.get_bind(),checkfirst=True)


def downgrade():
    for model in (ReviewLibraryAuditRow,VerifiedReplyContext,VerifiedReplyText):
        model.__table__.drop(op.get_bind(),checkfirst=True)
