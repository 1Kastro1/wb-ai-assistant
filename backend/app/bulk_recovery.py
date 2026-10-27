"""Frozen regeneration queue for excluded WB drafts. Never publishes a reply."""
import asyncio
import time
from uuid import uuid4
from sqlalchemy import select, func
from .db import Session, Action, Job, Review, Draft, Product, Publication, StoreProfile, GenerationOutcome, now
from .generation_control import batch_context, exclusive_generation, GenerationBusy, ReviewSkipped

KIND = 'wb_excluded_recovery'
ITEMS = 'wb_recovery_items'
TERMINAL = ('completed', 'cancelled', 'failed', 'interrupted')


def protected_draft(db, review, draft):
    from .draft_audit import manually_edited
    return (not review or not draft or review.is_answered or review.marketplace != 'wb'
            or review.status in ('publishing', 'pending_confirmation', 'published')
            or manually_edited(draft)
            or bool(db.scalar(select(Publication.id).where(Publication.review_id == review.id))))


def recovery_view(db, parent):
    job = db.get(Job, parent.payload.get('recovery_job_id', ''))
    if not job:
        return None
    state = job.result or {}
    return {'id': job.id, 'status': job.status, **{key: state.get(key, 0) for key in
            ('total', 'processed', 'ready', 'manual', 'errors', 'skipped', 'retrying')},
            'message': state.get('message', ''), 'detail': state.get('detail', ''),
            'current_review': state.get('current_review', ''), 'cancel_requested': job.cancel_requested}


def start(db, parent, actor_id, retry_errors=False):
    from .bulk_publication import MANIFEST_KIND
    from .bulk_exclusions import exclusion_rows
    db.scalar(select(StoreProfile).where(StoreProfile.id == parent.payload['account_id']).with_for_update())
    parent = db.scalar(select(Action).where(Action.id == parent.id).with_for_update().execution_options(populate_existing=True))
    if parent.status in ('preparing', 'sending'):
        raise ValueError('Дождитесь завершения сбора или отправки текущего пакета')
    previous = db.get(Job, parent.payload.get('recovery_job_id', ''))
    if previous and previous.status in ('queued', 'running'):
        return recovery_view(db, parent)
    other = db.scalar(select(Job.id).where(Job.kind == KIND, Job.status.in_(('queued', 'running')),
        Job.result['account_id'].as_string() == parent.payload['account_id']))
    if other:
        raise ValueError('В этом магазине уже готовятся исключённые отзывы. Дождитесь завершения')
    if previous and previous.status in ('cancelled', 'failed', 'interrupted') and not retry_errors:
        previous.status, previous.cancel_requested = 'queued', False
        previous.result = {**previous.result, 'retry_at': 0, 'failure_attempts': 0, 'detail': ''}
        db.commit()
        return recovery_view(db, parent)
    preparation = db.get(Job, parent.payload['prepare_job_id'])
    cutoff = (preparation.result or {}).get('cursor', '') if preparation else ''
    if not cutoff:
        raise ValueError('В пакете ещё нет просмотренных отзывов')
    accepted = exclusion_rows(db, parent.id, MANIFEST_KIND)
    query = select(Review.id, Draft.id).join(Draft, Draft.review_id == Review.id).where(
        Review.wb_account_id == parent.payload['account_id'], Review.marketplace == 'wb',
        Review.is_answered == False, Review.id <= cutoff,
        Review.id.not_in(select(accepted.c.review_id)))
    if retry_errors:
        if not previous:
            raise ValueError('Сначала подготовьте исключённые отзывы')
        failed = exclusion_rows(db, previous.id, ITEMS)
        query = query.where(Review.id.in_(select(failed.c.review_id).where(failed.c.reason == 'error')))
    # Snapshot just IDs in bounded chunks, never an unbounded job JSON field.
    rows = db.execute(query.order_by(Review.id)).all()
    if not rows:
        raise ValueError('Нет отзывов для повторной подготовки')
    jid = str(uuid4())
    job = Job(id=jid, kind=KIND, max_attempts=3, priority=45, result={
        'parent_id': parent.id, 'account_id': parent.payload['account_id'], 'requested_by': actor_id,
        'total': len(rows), 'processed': 0, 'ready': 0, 'manual': 0, 'errors': 0, 'skipped': 0,
        'message': 'Подготовка новых ответов поставлена в очередь'})
    db.add(job)
    for offset in range(0, len(rows), 250):
        db.add(Action(id=str(uuid4()), kind=ITEMS, status='completed', payload={
            'parent_id': jid, 'account_id': parent.payload['account_id'], 'start_index': offset,
            'end_index': min(len(rows), offset + 250), 'items': [
                {'review_id': rid, 'draft_id': did, 'reason': 'pending'} for rid, did in rows[offset:offset + 250]]}, result={}))
    audit = db.get(Job, parent.payload.get('exclusions_job_id', ''))
    if audit and audit.status in ('queued', 'running'):
        audit.cancel_requested = True
        if audit.status == 'queued':
            audit.status = 'cancelled'
    parent.payload = {**parent.payload, 'recovery_job_id': jid}
    db.commit()
    return recovery_view(db, parent)


def stop(db, parent):
    job = db.get(Job, parent.payload.get('recovery_job_id', ''))
    if job and job.status in ('queued', 'running'):
        job.cancel_requested = True
        if job.status == 'queued':
            job.status = 'cancelled'
        db.commit()
    return recovery_view(db, parent)


async def run_step(job_id):
    started = time.monotonic()
    for _ in range(25):
        await step(job_id)
        with Session() as db:
            job = db.get(Job, job_id)
            if job.status != 'queued' or job.result.get('retry_at', 0) > time.time() or time.monotonic() - started >= 30:
                return


async def check_saved(db, review, draft, account_id):
    """Retry a checker outage on the saved text, without generating it again."""
    from .services import assess_reply, trusted_product_facts
    from .owner_reply_rules import matching_owner_rules
    from .owner_reply_policy import enforce_owner_rules, attach_rule_quality
    rid, did = review.id, draft.id
    expected = (draft.revision, draft.text, review.text, review.rating, review.product_id)
    product = db.get(Product, review.product_id)
    rules = matching_owner_rules(db, product)
    facts = trusted_product_facts(db, product)
    assess = lambda value: assess_reply(review, product, value, facts)
    answer, verdict = await enforce_owner_rules(draft.text, review, product, rules, facts, assess,
        check_only=True, allow_fast_positive=True, check_seconds=120)
    db.rollback()
    review = db.scalar(select(Review).where(Review.id == rid).with_for_update().execution_options(populate_existing=True))
    draft = db.scalar(select(Draft).where(Draft.id == did).with_for_update().execution_options(populate_existing=True))
    if (protected_draft(db, review, draft) or review.wb_account_id != account_id or expected != (draft.revision, draft.text, review.text, review.rating, review.product_id)
            or answer != draft.text or rules != matching_owner_rules(db, db.get(Product, review.product_id))):
        raise ReviewSkipped('Ответ или правила изменились во время проверки; сохранены актуальные данные')
    draft.quality = attach_rule_quality(assess(draft.text), verdict)
    review.status = 'draft_ready' if draft.quality['passed'] and not review.manual else 'manual_review'
    db.commit()


async def step(job_id):
    from .services import generate_draft
    from .bulk_publication import exclusion_reason
    from .owner_reply_rules import matching_owner_rules
    from .owner_reply_policy import saved_rule_quality
    with Session() as db:
        job = db.get(Job, job_id)
        if job.cancel_requested:
            job.status = 'cancelled'; db.commit(); return
        position = job.result.get('processed', 0)
        chunk = db.scalar(select(Action).where(Action.kind == ITEMS, Action.payload['parent_id'].as_string() == job_id,
            Action.payload['start_index'].as_integer() <= position, Action.payload['end_index'].as_integer() > position))
        if not chunk:
            job.status, job.progress = 'completed', 100; db.commit(); return
        index = position - chunk.payload['start_index']
        item = chunk.payload['items'][index]
        rid, chunk_id = item['review_id'], chunk.id
        account_id = job.result['account_id']
        job.result = {**job.result, 'current_review': rid, 'message': 'Готовим новый ответ и проверяем правила'}
        db.commit()
    outcome, detail, again = 'skipped', '', False
    try:
        with Session() as db:
            review, draft = db.get(Review, rid), db.get(Draft, item['draft_id'])
            if protected_draft(db, review, draft) or review.wb_account_id != account_id:
                outcome, detail = 'manual', 'Ручные правки, опубликованный ответ или начатая публикация сохранены'
            else:
                marker = db.get(GenerationOutcome, (job_id, rid))
                if not marker:
                    token = batch_context.set({'job_id': job_id})
                    try:
                        await generate_draft(db, rid, recovery=account_id)
                    finally:
                        batch_context.reset(token)
                else:
                    async def verify():
                        db.expire_all()
                        await check_saved(db, db.get(Review, rid), db.get(Draft, item['draft_id']), account_id)
                    await exclusive_generation(rid, verify)
                db.expire_all()
                review, draft = db.get(Review, rid), db.get(Draft, item['draft_id'])
                rules = matching_owner_rules(db, db.get(Product, review.product_id))
                verdict = saved_rule_quality(draft.text, rules, (draft.quality or {}).get('owner_rules'), review=review)
                reason = exclusion_reason(db, review, draft, rules)
                if verdict.get('technical_error'):
                    outcome, detail, again = 'error', verdict.get('reason', 'Сервис проверки недоступен'), True
                else:
                    attempted = db.scalar(select(Publication.id).where(Publication.review_id == rid))
                    outcome, detail = ('ready', '') if not reason and not attempted else ('manual', reason or 'Публикация уже начата')
    except GenerationBusy as error:
        detail, again = str(error), True
        outcome = 'error'
    except ReviewSkipped as error:
        outcome, detail = 'manual', str(error)
    except Exception as error:
        outcome, detail, again = 'error', (str(error) or type(error).__name__)[:400], True
    with Session() as db:
        job = db.scalar(select(Job).where(Job.id == job_id).with_for_update())
        if job.cancel_requested:
            job.status = 'cancelled'; db.commit(); return
        state = dict(job.result)
        attempt = state.get('item_attempt', 0) + 1
        if again and attempt < 3:
            job.result = {**state, 'item_attempt': attempt, 'retrying': 1, 'last_served_at': now(),
                'retry_at': time.time() + 30 * attempt, 'message': 'Ответ сохранён; повторяем проверку после сбоя', 'detail': detail}
            job.status = 'queued'; db.commit(); return
        chunk = db.scalar(select(Action).where(Action.id == chunk_id).with_for_update())
        items = [dict(value) for value in chunk.payload['items']]
        items[index] = {**items[index], 'reason': outcome, 'detail': detail, 'at': now()}
        chunk.payload = {**chunk.payload, 'items': items}
        state[outcome if outcome != 'error' else 'errors'] = state.get(outcome if outcome != 'error' else 'errors', 0) + 1
        state.update(processed=position + 1, retrying=0, item_attempt=0, retry_at=0, last_served_at=now(),
            current_review='', detail='', message='Новые ответы сохранены; отправка только после подтверждения')
        job.result = state
        job.progress = round(100 * (position + 1) / max(1, state['total']))
        job.status = 'completed' if position + 1 >= state['total'] else 'queued'
        db.commit()
