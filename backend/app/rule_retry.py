"""Retry verification of saved text; never regenerate or publish a reply."""
from uuid import uuid4
from sqlalchemy import select
from .db import Job


def technical_check(quality):
    rules = (quality or {}).get('owner_rules') or {}
    return not rules.get('passed') and bool(rules.get('technical_error') or rules.get('status') == 'error')


def enqueue(db, draft, review, actor_id=''):
    # Caller holds the draft row lock (or its generation lease).
    existing = db.scalar(select(Job).where(Job.kind == 'draft_recheck',
        Job.status.in_(('queued', 'running')),
        Job.result['retry_draft_id'].as_string() == draft.id))
    if existing:
        return existing
    job = Job(id=str(uuid4()), kind='draft_recheck', priority=60, max_attempts=10,
        result={'account_id': review.wb_account_id, 'draft_ids': [draft.id],
                'retry_draft_id': draft.id, 'check_only': True, 'requested_by': actor_id,
                'message': 'Повторная проверка правил: сохранённый текст не изменяется'})
    db.add(job)
    return job
