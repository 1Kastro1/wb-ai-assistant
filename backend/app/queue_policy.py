"""Cooperative, durable scheduling. Never discard a job to let another run."""
import time
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, func, case, or_
from .db import Job


class QueueYield(Exception):
    """The current import committed its cursor and can safely resume later."""


def next_job(db, kinds=None):
    statement = select(Job).where(Job.status == 'queued', Job.cancel_requested == False,
        func.coalesce(Job.result['retry_at'].as_float(), 0) <= time.time())
    # Archive inspection is ordinary resumable background work. Safe mode,
    # assistant state and publication consent retain their own independent gates.
    # Never suppress checks needed by the publisher, or new-review imports.
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
    overdue_send = Job.kind.in_(('wb_publish_send', 'scheduled_publication', 'publication_reconcile')) & (served < cutoff)
    generation_cutoff = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
    batch_generation = or_(Job.kind.in_(('drafts', 'question_drafts','review_library_audit')),
        (Job.kind == 'draft_recheck') & or_(Job.result['policy_scan'].as_boolean() == True,
            Job.result['policy_regeneration'].as_boolean() == True,
            (Job.result['positive_recheck'].as_boolean() == True) & (Job.result['background'].as_boolean() == True)))
    manual_check = ((Job.result['retry_draft_id'].as_string().is_not(None))
                    & (Job.result['manual_check'].as_boolean() == True))
    direct_waiting = select(Job.id).where(Job.status == 'queued', Job.cancel_requested == False,
        or_(Job.kind.in_(('single_draft', 'single_question_draft')),
            manual_check),
        func.coalesce(Job.result['retry_at'].as_float(), 0) <= time.time()).correlate(None).exists()
    overdue_generation = batch_generation & (served < generation_cutoff) & ~direct_waiting
    # Imports must age into the same fair, oldest-served band. Otherwise a
    # continuous rotation of model checks/publication can starve fresh reviews
    # for days, even though the import's numeric priority is reasonable.
    # Each import yields only after committing its resume checkpoint.
    overdue_sync = Job.kind.in_(('products', 'reviews', 'questions')) & (served < generation_cutoff) & ~direct_waiting
    fresh_cutoff = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    fresh_due = (Job.kind.in_(('drafts','question_drafts')) &
        (Job.result['fresh_reviews'].as_boolean()==True) & (served < fresh_cutoff) & ~direct_waiting)
    # Fresh work ages sooner, but shares the oldest-served band: three long
    # fresh batches must never permanently exclude checks, archive or sends.
    interactive = case((or_(fresh_due, overdue_send, overdue_generation, overdue_sync), -2), (urgent_positive, -1),
                       (Job.kind.in_(('single_draft', 'single_question_draft')), 0),
                       (manual_check, 1),
                       (Job.kind == 'wb_excluded_recovery', 2), else_=3)
    requested_check = case((Job.result['retry_draft_id'].as_string().is_not(None),
        func.coalesce(Job.result['requested_check_at'].as_string(), Job.created_at)), else_='')
    return (interactive, requested_check.desc(), served, Job.priority, Job.created_at, Job.id)


def other_waiting(db, job_id):
    return bool(db.scalar(select(Job.id).where(Job.id != job_id, Job.status == 'queued',
        Job.cancel_requested == False, func.coalesce(Job.result['retry_at'].as_float(), 0) <= time.time()).limit(1)))

