"""Cooperative, durable scheduling. Never discard a job to let another run."""
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func, case
from .db import Job


class QueueYield(Exception):
    """The current import committed its cursor and can safely resume later."""


def next_job(db, kinds=None):
    statement = select(Job).where(Job.status == 'queued', Job.cancel_requested == False,
        func.coalesce(Job.result['retry_at'].as_float(), 0) <= time.time())
    if kinds is not None:
        statement = statement.where(Job.kind.in_(kinds))
    return db.scalar(statement.order_by(*queue_ordering()).limit(1))


def queue_ordering():
    # Direct clicks first, then the owner's priority WB recovery. A retry whose
    # backoff has not expired stays out of the queue; imports can use that gap.
    urgent_positive = ((Job.result['positive_recheck'].as_boolean() == True)
                       & Job.result['background'].as_boolean().is_not(True))
    served = func.coalesce(Job.result['last_served_at'].as_string(), Job.created_at)
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()
    # An already approved publication gets one ordinary slice after waiting.
    # Its next checkpoint refreshes last_served_at, so other work still runs.
    overdue_send = (Job.kind == 'wb_publish_send') & (served < cutoff)
    interactive = case((overdue_send, -2), (urgent_positive, -1),
                       (Job.kind.in_(('single_draft', 'single_question_draft')), 0),
                       (Job.result['retry_draft_id'].as_string().is_not(None), 1),
                       (Job.kind == 'wb_excluded_recovery', 2), else_=3)
    requested_check = case((Job.result['retry_draft_id'].as_string().is_not(None), Job.created_at), else_='')
    return (interactive, requested_check.desc(), served, Job.priority, Job.created_at, Job.id)


def other_waiting(db, job_id):
    return bool(db.scalar(select(Job.id).where(Job.id != job_id, Job.status == 'queued',
        Job.cancel_requested == False, func.coalesce(Job.result['retry_at'].as_float(), 0) <= time.time()).limit(1)))
