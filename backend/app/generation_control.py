"""Exclusive, renewable generation leases, including across API/worker processes."""
import asyncio
import time
from uuid import uuid4
from contextvars import ContextVar
from sqlalchemy import delete, update
from sqlalchemy.exc import IntegrityError
from .db import Session, GenerationClaim, GenerationOutcome

batch_context = ContextVar('batch_generation_context', default=None)


def record_outcome(db, review, draft):
    context = batch_context.get()
    if context:
        db.merge(GenerationOutcome(job_id=context['job_id'], review_id=review.id,
                                   attention=review.status == 'manual_review'))
        if context.get('requested_by'):
            draft.created_by = draft.created_by or context['requested_by']
            draft.updated_by = context['requested_by']


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
