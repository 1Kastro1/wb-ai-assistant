"""One durable review repair worker per connected marketplace store."""
import time

from sqlalchemy import and_, case, func, or_, select

from .db import Account, Draft, IS_SQLITE, Job, Publication, Review, ReviewLibraryAuditRow, Session, StoreProfile, now, setting
from .generation_control import GenerationBusy
from .rule_retry import MAX_CHECK_FAILURES, retry_delay, verification_failure

KIND = 'review_minion'
PROVIDERS = ('wb', 'ozon', 'yandex')


def enqueue(db, store):
    job_id = f'{KIND}:{store.id}'
    job = db.get(Job, job_id)
    if job is None:
        if IS_SQLITE:
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        db.execute(insert(Job).values(id=job_id, kind=KIND, priority=220, result={
            'account_id': store.id, 'automated_repair': True,
            'message': 'Миньон готов проверять сложные отзывы',
        }).on_conflict_do_nothing(index_elements=['id']))
    elif job.status == 'failed' and not job.cancel_requested:
        from datetime import datetime
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update()
            .execution_options(populate_existing=True))
        if job.status != 'failed' or job.cancel_requested:
            return
        state = job.result or {}
        finished = state.get('finished_at')
        if finished and datetime.fromisoformat(finished).timestamp() + 300 > time.time():
            return
        restart(job)


def restart(job):
    state = {**(job.result or {})}
    for key in ('failure_attempts', 'finished_at', 'error_type', 'detail', 'retry_at', 'wait_reason'):
        state.pop(key, None)
    job.status, job.cancel_requested = 'queued', False
    job.result = {**state, 'message': 'Миньон возобновит проверку отзывов'}


def enabled_for(db, schedule):
    return (schedule.get('enabled') and schedule.get('drafts_enabled')
        and setting(db, 'review_minions_enabled', False) and setting(db, 'assistant_enabled', True))


def eligible_review():
    return and_(
        Review.marketplace.in_(PROVIDERS), Review.is_answered == False,
        Review.status.in_(('manual_review', 'draft_ready')), Review.manual == False,
        Review.safety_critical == False, Review.risk == 'NORMAL',
        ~and_(Review.marketplace == 'ozon', func.trim(Review.text) == ''),
        ~select(Publication.id).where(Publication.review_id == Review.id,
            Publication.status != 'retryable').exists(),
    )


def interrupted_text_check():
    proof = Draft.quality['owner_rules']
    return and_(func.trim(Review.text) != '', or_(
        and_(Draft.quality_technical == True, or_(
            func.coalesce(proof['retryable'].as_boolean(), True),
            proof['failure_code'].as_string() == 'retry_exhausted')),
        func.coalesce(proof['continuation'].as_boolean(), False),
    ))


def handoff_context(db, draft, review):
    """Route a stale publication candidate to the ordinary repair worker."""
    return handoff(db, draft, review, context_changed=True)


def handoff(db, draft, review, *, context_changed=False):
    """Wake the durable store worker after a retryable verification failure."""
    proof = (draft.quality or {}).get('owner_rules') or {}
    if not enabled_for(db, setting(db, 'automation_schedule', {})):
        return None
    if not context_changed and (not proof.get('technical_error') or proof.get('continuation')
        or (proof.get('retryable') is False and proof.get('failure_code') != 'retry_exhausted')
        or not (review.text or '').strip() or review.marketplace not in PROVIDERS):
        return None
    store = db.get(StoreProfile, review.wb_account_id)
    if not store or store.provider != review.marketplace or not db.get(Account, store.id):
        return None
    if not db.scalar(select(Review.id).where(Review.id == review.id, eligible_review())):
        return None
    enqueue(db, store)
    job = db.scalar(select(Job).where(Job.id == f'{KIND}:{store.id}').with_for_update()
        .execution_options(populate_existing=True))
    if job.status not in ('queued', 'running') or job.cancel_requested:
        return None
    audit = db.get(ReviewLibraryAuditRow, (job.id, review.id))
    if not audit:
        audit = ReviewLibraryAuditRow(job_id=job.id, review_id=review.id)
        db.add(audit)
    if context_changed:
        from .review_context import context_proof
        key = context_proof(review, draft.text)
        if audit.status in ('retry', 'ready_to_deliver') or (
                audit.status == 'handoff' and (audit.verification_progress or {}).get('_context_handoff') == key):
            return job
    elif (audit.verification_progress or {}).get('_minion_revision') == draft.revision:
        if audit.status == 'retry':
            return job  # Keep the repair proposal and any model backoff.
        if audit.status not in ('handoff', 'verified'):
            return job  # This exact revision already has a decision.
    audit.status, audit.attempts, audit.retry_at = 'handoff', 0, 0
    audit.verification_progress = {'_minion_revision': draft.revision}
    if context_changed:
        audit.verification_progress = {**audit.verification_progress, '_context_handoff': key}
    job.result = {**job.result, 'handoff_at': now(), 'retry_at': 0,
        'message': 'Миньон подхватил прерванную проверку'}
    return job


def continuation_check(current):
    audit = ReviewLibraryAuditRow
    progress = audit.verification_progress
    return and_(audit.status == 'retry', audit.retry_at <= current,
        progress['_minion_revision'].as_integer() == Draft.revision,
        or_(progress['continuation'].as_boolean() == True,
            progress['_minion_pending_repair']['proof']['continuation'].as_boolean() == True))


def candidate(db, job, current):
    audit = ReviewLibraryAuditRow
    changed = func.coalesce(audit.verification_progress['_minion_revision'].as_integer(), -1) != Draft.revision
    handed_off = func.coalesce(audit.status == 'handoff', False)
    fresh = or_(audit.review_id.is_(None), changed, handed_off)
    retry = and_(audit.status == 'retry', audit.retry_at <= current)
    prefer_fresh = job.result.get('last_lane') == 'retry'
    progress = audit.verification_progress
    repaired = progress['_minion_pending_repair']['proof']
    remaining = (func.coalesce(repaired['semantic_groups'].as_integer(), progress['semantic_groups'].as_integer(), 1000)
        - func.coalesce(repaired['verified_groups'].as_integer(), progress['verified_groups'].as_integer(), 0))
    continuing = func.coalesce(continuation_check(current), False)
    order = [continuing.desc(), case((continuing, remaining), else_=0), handed_off.desc(),
        fresh.desc() if prefer_fresh else fresh.asc(), Review.created_at.desc(), Review.id]
    return db.scalar(select(Review.id).join(Draft, Draft.review_id == Review.id)
        .outerjoin(audit, and_(audit.job_id == job.id, audit.review_id == Review.id))
        .where(
            Review.wb_account_id == job.result['account_id'], eligible_review(),
            or_(and_(Draft.quality_attention == True, Draft.quality_technical == False),
                interrupted_text_check(), audit.status.in_(('retry', 'handoff'))),
            or_(fresh, retry),
        ).order_by(*order).limit(1))


async def run_step(job_id, session_factory=Session):
    from .review_library_audit import inspect_review

    with session_factory() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if not job or job.cancel_requested:
            return
        schedule = setting(db, 'automation_schedule', {})
        enabled = enabled_for(db, schedule)
        delivery = db.scalar(select(ReviewLibraryAuditRow).where(
            ReviewLibraryAuditRow.job_id == job_id, ReviewLibraryAuditRow.status == 'ready_to_deliver')
            .order_by(ReviewLibraryAuditRow.updated_at, ReviewLibraryAuditRow.review_id).limit(1))
        review_id = delivery.review_id if delivery else candidate(db, job, time.time()) if enabled else None
        if not review_id:
            job.status = 'queued'
            job.result = {**job.result, 'handoff_at': None, 'continuation_pending': False, 'retry_at': time.time() + 120,
                'wait_reason': 'idle' if enabled else 'disabled',
                'message': 'Ожидает сложных отзывов' if enabled else 'Подготовка ответов выключена'}
            db.commit()
            return
        revision = ((delivery.verification_progress or {}).get('_minion_revision') if delivery else
            db.scalar(select(Draft.revision).where(Draft.review_id == review_id)))
        prior = db.get(ReviewLibraryAuditRow, (job_id, review_id))
        same_revision = bool(prior and (prior.verification_progress or {}).get('_minion_revision') == revision)
        previous_attempts = prior.attempts if same_revision else 0
        if prior and not same_revision:
            prior.verification_progress = None
            db.commit()

    try:
        if delivery:
            outcome = {'status': 'verified', 'input_key': delivery.input_key, 'template_id': delivery.template_id}
        else:
            outcome = await inspect_review(job_id, review_id, session_factory, live_minion=True)
    except GenerationBusy:
        outcome = {'status': 'retry', 'issues': ['Черновик уже обрабатывается']}
    except (TimeoutError, ValueError) as error:
        failure = verification_failure(error)
        if failure['failure_code'] == 'verification_error' and not str(error).startswith((
                'Некорректный ответ модели', 'Локальная модель не завершила ответ')):
            raise
        outcome = {'status': 'retry' if failure['retryable'] or failure['failure_code'] == 'verification_error'
            else 'technical_attention', 'issues': [
                'Модель вернула неполный ответ' if failure['failure_code'] == 'verification_error' else failure['reason']]}
    except Exception as error:
        from .batch_processing import transient
        if not transient(error):
            raise
        outcome = {'status': 'retry', 'issues': [verification_failure(error)['reason']]}

    with session_factory() as db:
        job = db.get(Job, job_id)
        if not job or job.cancel_requested:
            return
        checked_revision = revision + int(bool(outcome.get('auto_repaired')))
        attempts = previous_attempts + int(not outcome.get('continuation'))
        stopped = outcome['status'] == 'technical_attention' or (
            outcome['status'] == 'retry' and attempts >= MAX_CHECK_FAILURES)
        if stopped:
            draft = db.scalar(select(Draft).where(Draft.review_id == review_id,
                Draft.revision == checked_revision).with_for_update())
            if draft and draft.quality_passed is not True:
                quality = draft.quality or {}
                rules = quality.get('owner_rules') or {}
                draft.quality = {**quality, 'passed': False, 'owner_rules': {
                    **rules, 'status': 'error', 'retryable': False, 'technical_error': True,
                    'continuation': False, 'checked': False, 'passed': False, 'retry_at': 0,
                    'failure_code': 'retry_exhausted' if outcome['status'] == 'retry' else
                        rules.get('failure_code') or 'minion_stopped'}}
        if outcome['status'] == 'verified':
            from .scheduled_publication import queue_minion_reply
            queue_minion_reply(db, review_id, checked_revision)
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update()
            .execution_options(populate_existing=True))
        if job.cancel_requested:
            return
        row = db.get(ReviewLibraryAuditRow, (job_id, review_id))
        if row is None:
            row = ReviewLibraryAuditRow(job_id=job_id, review_id=review_id, status='retry')
            db.add(row)
        row.attempts = attempts
        row.status = 'technical_attention' if stopped else outcome['status']
        row.retry_at = time.time() + (0 if outcome.get('continuation') else retry_delay(row.attempts)) if row.status == 'retry' else 0
        row.input_key = outcome.get('input_key', '')
        row.template_id = outcome.get('template_id', '')
        row.issues = outcome.get('issues', [])
        row.updated_at = now()
        row.verification_progress = {**(row.verification_progress or {}), '_minion_revision': checked_revision}
        db.flush()
        more_handoffs = db.scalar(select(ReviewLibraryAuditRow.review_id)
            .join(Review, Review.id == ReviewLibraryAuditRow.review_id).join(Draft, Draft.review_id == Review.id)
            .where(ReviewLibraryAuditRow.job_id == job.id, ReviewLibraryAuditRow.status == 'handoff',
                eligible_review()).limit(1))
        more_continuations = db.scalar(select(ReviewLibraryAuditRow.review_id)
            .join(Review, Review.id == ReviewLibraryAuditRow.review_id).join(Draft, Draft.review_id == Review.id)
            .where(ReviewLibraryAuditRow.job_id == job.id, eligible_review(), continuation_check(time.time())).limit(1))
        job.status = 'queued'
        state = {k: v for k, v in job.result.items() if k not in ('failure_attempts', 'error_type', 'detail')}
        new_handoff = state.get('handoff_at') if more_handoffs else None
        job.result = {**state, 'processed': job.result.get('processed', 0) + int(row.status != 'retry'),
            'verified': job.result.get('verified', 0) + int(row.status == 'verified'),
            'needs_person': job.result.get('needs_person', 0) + int(row.status in ('attention', 'blocked', 'technical_attention')),
            'handoff_at': new_handoff, 'continuation_pending': bool(more_continuations),
            'retry_at': 0 if new_handoff or more_continuations else time.time() + 5,
            'last_progress_at': now(), 'wait_reason': 'cadence',
            'last_lane': 'retry' if same_revision else 'new',
            'message': 'Миньон завершил этап проверки'}
        learn_case = row.status == 'attention' and bool(row.issues) and setting(db, 'auto_case_rules_enabled', False)
        db.commit()

    if learn_case:
        # A scoped policy change can queue a new audit. Save the minion's
        # result first so it cannot overwrite that audit's updated state.
        from .library_workspace import suggest_case_rule
        with session_factory() as db:
            draft = db.scalar(select(Draft).where(Draft.review_id == review_id))
            review = db.get(Review, review_id)
            if draft and draft.revision == checked_revision and review and not review.is_answered:
                row = db.get(ReviewLibraryAuditRow, (job_id, review_id))
                if row:
                    suggest_case_rule(db, row, review)
                    db.commit()
