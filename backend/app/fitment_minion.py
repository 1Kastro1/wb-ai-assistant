"""Server-owned VIN/FRAME work, with one durable cursor per store."""
import time
from datetime import datetime, timedelta, timezone
from sqlalchemy import and_, or_, select
from .db import Account, Draft, Job, Review, Session, StoreProfile, setting
from .generation_control import GenerationBusy, ReviewSkipped
from .vehicle_identifiers import vehicle_identifiers

KIND = 'fitment_minion'
MARKETS = ('wb', 'ozon', 'yandex')
PAGE_SIZE = 40


def enabled(db, schedule):
    return bool(schedule.get('enabled') and schedule.get('question_drafts_enabled')
                and setting(db, 'assistant_enabled', True))


def enqueue(db, store, schedule):
    if store.provider not in MARKETS or not enabled(db, schedule):
        return
    job_id = f'{KIND}:{store.id}'
    if not db.get(Job, job_id):
        db.add(Job(id=job_id, kind=KIND, priority=235, max_attempts=1000000,
            result={'account_id': store.id, 'processed': 0, 'confirmed': 0, 'pending_catalog': 0,
                    'message': 'Миньон подбора деталей ожидает вопросы с VIN или FRAME'}))


async def run_step(job_id, session_factory=Session):
    from .services import generate_draft
    from .production_date import single_request

    with session_factory() as db:
        job = db.get(Job, job_id)
        if not job or job.cancel_requested:
            return
        state = job.result or {}
        store = db.get(StoreProfile, state.get('account_id'))
        schedule = setting(db, 'automation_schedule', {})
        if not store or not db.get(Account, store.id) or not enabled(db, schedule):
            job.status = 'queued'
            job.result = {**state, 'retry_at': time.time() + 120, 'wait_reason': 'disabled'}
            db.commit()
            return
        recent = (datetime.now(timezone.utc) - timedelta(days=max(1, int(schedule.get('drafts_days', 7))))).isoformat()
        query = select(Review).outerjoin(Draft, Draft.review_id == Review.id).where(
            Review.wb_account_id == store.id, Review.marketplace == store.provider + '_question',
            Review.is_answered == False, Review.status != 'publishing', Review.manual == False,
            or_(Review.created_at >= recent, Review.received_at >= recent), Draft.id.is_(None))
        if cursor := state.get('cursor'):
            query = query.where(or_(Review.created_at < cursor[0],
                and_(Review.created_at == cursor[0], Review.id > cursor[1])))
        rows = list(db.scalars(query.order_by(Review.created_at.desc(), Review.id).limit(PAGE_SIZE)))
        cursor = None
        for question in rows:
            cursor = [question.created_at, question.id]
            if not vehicle_identifiers(question.text) or single_request(question):
                continue
            try:
                draft = await generate_draft(db, question.id, skip_existing=True, catalog_only=True)
            except ReviewSkipped:
                continue
            except GenerationBusy:
                job.result = {**state, 'retry_at': time.time() + 15, 'wait_reason': 'cadence'}
                break
            fitment = (draft.get('quality') or {}).get('fitment') or {}
            status = fitment.get('status')
            state = {**state, 'processed': state.get('processed', 0) + 1,
                'confirmed': state.get('confirmed', 0) + int(status in ('FIT', 'ALTERNATIVE')),
                'pending_catalog': state.get('pending_catalog', 0) + int(status in ('NEEDS_CATALOG', 'CONFLICT')),
                'message': 'Подбор проверен по каталогу' if status in ('FIT', 'ALTERNATIVE')
                    else 'Вопрос сохранён для проверки по каталогу'}
            job.result = {**state, 'cursor': cursor, 'retry_at': time.time() + 10, 'wait_reason': 'cadence'}
            break
        else:
            job.result = {**state, 'cursor': cursor if len(rows) == PAGE_SIZE else None,
                'retry_at': time.time() + (10 if len(rows) == PAGE_SIZE else 120), 'wait_reason': 'idle'}
        job.status = 'queued'
        db.commit()
