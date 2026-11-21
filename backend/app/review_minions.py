"""One durable review repair worker per connected marketplace store."""
import time

from sqlalchemy import and_, func, or_, select

from .db import Draft, IS_SQLITE, Job, Publication, Review, ReviewLibraryAuditRow, Session, now, setting
from .generation_control import GenerationBusy
from .rule_retry import retry_delay

KIND = 'review_minion'
PROVIDERS = ('wb', 'ozon', 'yandex')


def enqueue(db, store):
    job_id = f'{KIND}:{store.id}'
    if db.get(Job, job_id) is None:
        if IS_SQLITE:
            from sqlalchemy.dialects.sqlite import insert
        else:
            from sqlalchemy.dialects.postgresql import insert
        db.execute(insert(Job).values(id=job_id, kind=KIND, priority=220, result={
            'account_id': store.id, 'automated_repair': True,
            'message': 'Миньон готов проверять сложные отзывы',
        }).on_conflict_do_nothing(index_elements=['id']))


def candidate(db, job, current):
    audit = ReviewLibraryAuditRow
    return db.scalar(select(Review.id).join(Draft, Draft.review_id == Review.id)
        .outerjoin(audit, and_(audit.job_id == job.id, audit.review_id == Review.id))
        .where(
            Review.wb_account_id == job.result['account_id'], Review.marketplace.in_(PROVIDERS),
            Review.is_answered == False, Review.status.in_(('manual_review', 'draft_ready')),
            Review.manual == False, Review.safety_critical == False, Review.risk == 'NORMAL',
            or_(and_(Draft.quality_attention == True, Draft.quality_technical == False), audit.status == 'retry'),
            ~and_(Review.marketplace == 'ozon', func.trim(Review.text) == ''),
            ~select(Publication.id).where(Publication.review_id == Review.id).exists(),
            or_(audit.review_id.is_(None), and_(audit.status == 'retry', audit.retry_at <= current)),
        ).order_by(Review.created_at.desc(), Review.id).limit(1))


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
                'message': 'Ожидает сложных отзывов' if enabled else 'Подготовка ответов выключена'}
            db.commit()
            return

    try:
        outcome = await inspect_review(job_id, review_id, session_factory, live_minion=True)
    except GenerationBusy:
        outcome = {'status': 'retry', 'issues': ['Черновик уже обрабатывается']}

    with session_factory() as db:
        job = db.get(Job, job_id)
        if not job or job.cancel_requested:
            return
        row = db.get(ReviewLibraryAuditRow, (job_id, review_id))
        if row is None:
            row = ReviewLibraryAuditRow(job_id=job_id, review_id=review_id, status='retry')
            db.add(row)
        row.attempts = (row.attempts or 0) + (0 if outcome.get('continuation') else 1)
        row.status = outcome['status']
        if row.status == 'retry' and row.attempts >= 5:
            row.status = 'technical_attention'
        row.retry_at = time.time() + (0 if outcome.get('continuation') else retry_delay(row.attempts)) if row.status == 'retry' else 0
        row.input_key = outcome.get('input_key', '')
        row.template_id = outcome.get('template_id', '')
        row.issues = outcome.get('issues', [])
        row.updated_at = now()
        job.status = 'queued'
        job.result = {**job.result, 'processed': job.result.get('processed', 0) + int(row.status != 'retry'),
            'verified': job.result.get('verified', 0) + int(row.status == 'verified'),
            'needs_person': job.result.get('needs_person', 0) + int(row.status in ('attention', 'blocked', 'technical_attention')),
            'retry_at': time.time() + 5, 'last_progress_at': now(),
            'message': 'Миньон проверяет сложные отзывы'}
        db.commit()
