"""Exclusive, renewable generation leases, including across API/worker processes."""
import asyncio
import time
from uuid import uuid4
from contextvars import ContextVar
from sqlalchemy import delete, update, select
from sqlalchemy.exc import IntegrityError
from .db import Session, GenerationClaim, GenerationOutcome, Job, now

batch_context = ContextVar('batch_generation_context', default=None)


def record_outcome(db, review, draft):
    context = batch_context.get()
    if context:
        record_usage(db,context['job_id'],review.id,
            'template' if (draft.model or '').startswith(('verified-template','learned-candidate')) else
            'model' if (draft.quality or {}).get('timing',{}).get('route')=='model' else 'local',
            updated=(draft.revision or 1)>1)
        db.merge(GenerationOutcome(job_id=context['job_id'], review_id=review.id,
                                   attention=review.status == 'manual_review', category='generated'))
        if context.get('requested_by'):
            draft.created_by = draft.created_by or context['requested_by']
            draft.updated_by = context['requested_by']


def record_usage(db,job_id,review_id,route,*,updated=False):
    # Same transaction as the saved draft/outcome. A replay never increments it.
    job=db.scalar(select(Job).where(Job.id==job_id).with_for_update().execution_options(populate_existing=True))
    previous=db.get(GenerationOutcome,(job_id,review_id))
    if not job or previous and previous.category!='deferred_fast':return
    state=dict(job.result or {});usage=dict(state.get('usage') or {})
    usage[route]=int(usage.get(route,0))+1
    if route!='reused':
        usage['updated' if updated else 'created']=int(usage.get('updated' if updated else 'created',0))+1
    usage['measured_since']=usage.get('measured_since') or now()
    job.result={**state,'usage':usage,'last_progress_at':now()}


class GenerationBusy(ValueError):
    pass


class FastPathUnavailable(ValueError):
    pass


class ReviewSkipped(ValueError):
    pass


def acquire(review_id, token, session_factory=Session):
    with session_factory() as db:
        db.execute(delete(GenerationClaim).where(
            GenerationClaim.review_id == review_id, GenerationClaim.expires_at < time.time()))
        db.add(GenerationClaim(review_id=review_id, token=token, expires_at=time.time() + 60))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise GenerationBusy('Ответ на этот отзыв уже готовится. Дождитесь завершения') from None


async def exclusive_generation(review_id, operation, session_factory=Session):
    token = uuid4().hex
    acquire(review_id, token, session_factory)
    task = asyncio.create_task(operation())
    renewed = time.monotonic()
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=1)
            if done:
                return task.result()
            if time.monotonic() - renewed >= 10:
                with session_factory() as db:
                    changed = db.execute(update(GenerationClaim).where(
                        GenerationClaim.review_id == review_id, GenerationClaim.token == token,
                        GenerationClaim.expires_at >= time.time(),
                    ).values(expires_at=time.time() + 60)).rowcount
                    db.commit()
                if not changed:
                    raise GenerationBusy('Обработка отзыва передана другому заданию')
                renewed = time.monotonic()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        with session_factory() as db:
            db.execute(delete(GenerationClaim).where(
                GenerationClaim.review_id == review_id, GenerationClaim.token == token))
            db.commit()
