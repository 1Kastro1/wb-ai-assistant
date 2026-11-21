"""One durable review repair worker per connected marketplace store."""
import time

from sqlalchemy import and_, func, or_, select

from .db import Draft, IS_SQLITE, Job, Publication, Review, ReviewLibraryAuditRow, Session, now, setting
from .generation_control import GenerationBusy
from .rule_retry import retry_delay, verification_failure

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


def candidate(db, job, current):
    audit = ReviewLibraryAuditRow
    changed = func.coalesce(audit.verification_progress['_minion_revision'].as_integer(), -1) != Draft.revision
    fresh = or_(audit.review_id.is_(None), changed)
    retry = and_(audit.status == 'retry', audit.retry_at <= current)
    prefer_fresh = job.result.get('last_lane') == 'retry'
    return db.scalar(select(Review.id).join(Draft, Draft.review_id == Review.id)
        .outerjoin(audit, and_(audit.job_id == job.id, audit.review_id == Review.id))
        .where(
            Review.wb_account_id == job.result['account_id'], Review.marketplace.in_(PROVIDERS),
            Review.is_answered == False, Review.status.in_(('manual_review', 'draft_ready')),
            Review.manual == False, Review.safety_critical == False, Review.risk == 'NORMAL',
            or_(and_(Draft.quality_attention == True, Draft.quality_technical == False), audit.status == 'retry'),
            ~and_(Review.marketplace == 'ozon', func.trim(Review.text) == ''),
            ~select(Publication.id).where(Publication.review_id == Review.id).exists(),
            or_(fresh, retry),
        ).order_by(fresh.desc() if prefer_fresh else fresh.asc(), Review.created_at.desc(), Review.id).limit(1))


async def run_step(job_id, session_factory=Session):
    from .review_library_audit import inspect_review

    with session_factory() as db:
        job = db.get(Job, job_id)
        if not job or job.cancel_requested:
            return
        schedule = setting(db, 'automation_schedule', {})
        enabled = (schedule.get('enabled') and schedule.get('drafts_enabled')
            and setting(db, 'review_minions_enabled', False) and setting(db, 'assistant_enabled', True))
        review_id = candidate(db, job, time.time()) if enabled else None
        if not review_id:
            job.status = 'queued'
            job.result = {**job.result, 'retry_at': time.time() + 120,
                'wait_reason': 'idle' if enabled else 'disabled',
                'message': 'Ожидает сложных отзывов' if enabled else 'Подготовка ответов выключена'}
            db.commit()
            return
        revision = db.scalar(select(Draft.revision).where(Draft.review_id == review_id))
        prior = db.get(ReviewLibraryAuditRow, (job_id, review_id))
        same_revision = bool(prior and (prior.verification_progress or {}).get('_minion_revision') == revision)
        previous_attempts = prior.attempts if same_revision else 0
        if prior and not same_revision:
            prior.verification_progress = None
            db.commit()

    try:
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
        row = db.get(ReviewLibraryAuditRow, (job_id, review_id))
        if row is None:
            row = ReviewLibraryAuditRow(job_id=job_id, review_id=review_id, status='retry')
            db.add(row)
        row.attempts = (previous_attempts or 0) + (0 if outcome.get('continuation') else 1)
        row.status = outcome['status']
        if row.status == 'retry' and row.attempts >= 5:
            row.status = 'technical_attention'
        row.retry_at = time.time() + (0 if outcome.get('continuation') else retry_delay(row.attempts)) if row.status == 'retry' else 0
        row.input_key = outcome.get('input_key', '')
        row.template_id = outcome.get('template_id', '')
        row.issues = outcome.get('issues', [])
        row.updated_at = now()
        checked_revision = revision
        if outcome.get('auto_repaired'):
            checked_revision += 1  # inspect_review committed its own replacement
        row.verification_progress = {**(row.verification_progress or {}), '_minion_revision': checked_revision}
        job.status = 'queued'
        state = {k: v for k, v in job.result.items() if k not in ('failure_attempts', 'error_type', 'detail')}
        job.result = {**state, 'processed': job.result.get('processed', 0) + int(row.status != 'retry'),
            'verified': job.result.get('verified', 0) + int(row.status == 'verified'),
            'needs_person': job.result.get('needs_person', 0) + int(row.status in ('attention', 'blocked', 'technical_attention')),
            'retry_at': time.time() + 5, 'last_progress_at': now(), 'wait_reason': 'cadence',
            'last_lane': 'retry' if same_revision else 'new',
            'message': 'Миньон проверяет сложные отзывы'}
        db.commit()
