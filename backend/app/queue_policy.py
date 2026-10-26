"""Cooperative, durable scheduling. Never discard a job to let another run."""
import time
from sqlalchemy import select, func, case
from .db import Job


class QueueYield(Exception):
    """The current import committed its cursor and can safely resume later."""


def next_job(db, kinds=None):
    statement = select(Job).where(Job.status == 'queued', Job.cancel_requested == False,
        func.coalesce(Job.result['retry_at'].as_float(), 0) <= time.time())
    if kinds is not None:
        statement = statement.where(Job.kind.in_(kinds))
    # User requests come first; background jobs take turns at saved boundaries.
    interactive = case((Job.kind.in_(('single_draft', 'single_question_draft')), 0), else_=1)
    served = func.coalesce(Job.result['last_served_at'].as_string(), Job.created_at)
    return db.scalar(statement.order_by(interactive, served, Job.priority, Job.created_at, Job.id).limit(1))


def other_waiting(db, job_id):
    return bool(db.scalar(select(Job.id).where(Job.id != job_id, Job.status == 'queued',
        Job.cancel_requested == False, func.coalesce(Job.result['retry_at'].as_float(), 0) <= time.time()).limit(1)))
