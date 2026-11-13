"""Retry verification of saved text; never regenerate or publish a reply."""
from uuid import uuid4
from sqlalchemy import select
from .db import Job, now, IS_SQLITE


def automatic_queue(db, account):
    """One durable streaming checker per store, rather than one job per draft."""
    import hashlib
    from sqlalchemy.dialects.sqlite import insert as sqlite_insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    jid = 'verify-store-' + hashlib.sha256(account.encode()).hexdigest()[:24]
    state = {'account_id': account, 'check_only': True, 'policy_scan': True,
             'technical_scan': True, 'cursor_id': '', 'checked': 0,
             'message': 'Проверка сохранённых ответов магазина'}
    insert = sqlite_insert if IS_SQLITE else pg_insert
    db.execute(insert(Job).values(id=jid, kind='draft_recheck', status='queued', priority=65,
        max_attempts=1000000, result=state).on_conflict_do_nothing(index_elements=['id']))
    job = db.scalar(select(Job).where(Job.id == jid).with_for_update().execution_options(populate_existing=True))
    if job.status == 'running' and job.cancel_requested:
        return job
    if job.status not in ('queued', 'running') or job.cancel_requested:
        job.status, job.cancel_requested, job.progress, job.attempts = 'queued', False, 0, 0
        job.result = state
    return job


def technical_check(quality):
    rules = (quality or {}).get('owner_rules') or {}
    return (rules.get('retryable') is not False and not rules.get('passed')
            and bool(rules.get('continuation') or rules.get('technical_error') or rules.get('status') == 'error'))


def verification_failure(error):
    """Classify availability separately from an unchanged, invalid request.

    A saved reply is never approved by an outage. Invalid model JSON can recover
    on retry; exceeding the model context or missing credentials cannot recover
    by sending the exact same request forever.
    """
    import json
    import re
    import httpx
    from .batch_processing import transient
    message = str(error)
    if message.startswith(('Правила слишком длинные', 'Контекст модели переполнен')):
        return {'retryable': False, 'failure_code': 'context_limit',
                'reason': 'объём правил превышает контекст модели; сократите повторы или область применения'}
    status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
    if status in (400, 401, 403, 404, 413, 422) or re.search(r'\bHTTP (?:400|401|403|404|413|422)\b', message, re.I):
        return {'retryable': False, 'failure_code': 'service_configuration',
                'reason': 'сервис проверки отклонил запрос; проверьте настройки модели и доступ'}
    if isinstance(error, json.JSONDecodeError) or message.startswith((
            'Некорректный результат проверки правил', 'Проверка не указала нарушенное правило')):
        return {'retryable': True, 'failure_code': 'invalid_model_response',
                'reason': 'модель вернула некорректную проверку'}
    if transient(error):
        return {'retryable': True, 'failure_code': 'timeout' if isinstance(error, TimeoutError) else 'service_unavailable',
                'reason': 'истекло время проверки' if isinstance(error, TimeoutError) else 'сервис проверки недоступен'}
    return {'retryable': False, 'failure_code': 'verification_error',
            'reason': 'ошибка обработки проверки; требуется исправление сервиса'}


def durable_retry(job, error):
    # No overall expiration for a saved-text verification outage. Programming,
    # validation and authorization failures still follow the ordinary error path.
    from .batch_processing import transient
    return job.kind == 'draft_recheck' and bool((job.result or {}).get('check_only')) and transient(error)


def retry_delay(attempt):
    return (60, 120, 300)[min(2, max(0, int(attempt)-1))]


def enqueue(db, draft, review, actor_id='', *, manual=False):
    # Caller holds the draft row lock (or its generation lease).
    if not manual:
        return automatic_queue(db, review.wb_account_id)
    active = db.scalars(select(Job).where(Job.kind == 'draft_recheck',
        Job.status.in_(('queued', 'running')), Job.cancel_requested == False,
        Job.result['account_id'].as_string() == review.wb_account_id).order_by(Job.created_at, Job.id))
    for existing in active:
        state = existing.result or {}
        if manual and state.get('technical_scan') and existing.status == 'running' and state.get('current_draft_id') == draft.id:
            return existing
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


def consolidate(db):
    """Supersede redundant automatic checks, preserving explicit user checks."""
    changed = 0
    for job in list(db.scalars(select(Job).where(Job.kind == 'draft_recheck',
            Job.status.in_(('queued','running')), Job.cancel_requested == False))):
        state = job.result or {}
        if state.get('retry_draft_id') and state.get('check_only') and not state.get('manual_check'):
            target = automatic_queue(db, state.get('account_id') or 'owner')
            job.cancel_requested = True
            if job.status == 'queued': job.status = 'cancelled'
            job.result = {**state, 'superseded_by': target.id,
                'message': 'Проверка продолжится в общей очереди магазина'}
            changed += 1
    return changed

