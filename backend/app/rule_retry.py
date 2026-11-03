"""Retry verification of saved text; never regenerate or publish a reply."""
from uuid import uuid4
from sqlalchemy import select
from .db import Job, now


def technical_check(quality):
    rules = (quality or {}).get('owner_rules') or {}
    return not rules.get('passed') and bool(rules.get('technical_error') or rules.get('status') == 'error')


def durable_retry(job, error):
    # No overall expiration for a saved-text verification outage. Programming,
    # validation and authorization failures still follow the ordinary error path.
    from .batch_processing import transient
    return job.kind == 'draft_recheck' and bool((job.result or {}).get('check_only')) and transient(error)


def retry_delay(attempt):
    return (60, 120, 300)[min(2, max(0, int(attempt)-1))]


def enqueue(db, draft, review, actor_id='', *, manual=False):
    # Caller holds the draft row lock (or its generation lease).
    active = db.scalars(select(Job).where(Job.kind == 'draft_recheck',
        Job.status.in_(('queued', 'running')), Job.cancel_requested == False,
        Job.result['account_id'].as_string() == review.wb_account_id).order_by(Job.created_at, Job.id))
    for existing in active:
        state = existing.result or {}
        remaining = (state.get('draft_ids') or [])[int(state.get('cursor', 0)):]
        if state.get('check_only') and not state.get('policy_scan') and (
                state.get('retry_draft_id') == draft.id or remaining == [draft.id]):
            if manual:
                # A direct click resumes a delayed retry without duplicating a
                # running check or changing the saved reply/approval state.
                existing.result = {**state, 'manual_check': True, 'requested_by': actor_id,
                    'requested_check_at': now(),
                    **({'retry_at': 0, 'message': 'Повторная проверка поставлена в очередь'}
                       if existing.status == 'queued' else {})}
            return existing
    job = Job(id=str(uuid4()), kind='draft_recheck', priority=60, max_attempts=10,
        result={'account_id': review.wb_account_id, 'draft_ids': [draft.id],
                'retry_draft_id': draft.id, 'check_only': True, 'requested_by': actor_id, 'manual_check': manual,
                'message': 'Повторная проверка правил: сохранённый текст не изменяется'})
    if manual:
        job.result = {**job.result, 'manual_check': True, 'requested_check_at': now()}
    db.add(job)
    return job

