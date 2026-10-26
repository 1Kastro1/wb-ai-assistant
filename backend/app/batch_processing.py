"""Two independent draft lanes, fresh newest-first selection and durable checkpoints."""
import asyncio
import inspect
import re
import time
import httpx
from sqlalchemy import select, func, or_
from sqlalchemy.exc import OperationalError
from .db import Session, Job, Review, Draft, GenerationOutcome, Memory, now
from . import rule_execution
from .generation_control import FastPathUnavailable, GenerationBusy, ReviewSkipped, batch_context

POLL_SECONDS = .5
BATCH_SLICE_SECONDS = 60
RETRY_DELAYS = (5, 20)


def fast_policy_version(db):
    """Previously deferred IDs are eligible again after audited policy changes."""
    import hashlib
    import json
    rules = list(db.execute(select(Memory.id, Memory.scope, Memory.kind, Memory.text)
                           .where(Memory.enabled == True).order_by(Memory.id)))
    value = [rule_execution.VERSION, rule_execution.PROFILES, rule_execution.POSITIVE_BODIES,
             [list(row) for row in rules]]
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def fast_condition():
    blank = func.trim(func.replace(func.replace(func.replace(func.coalesce(Review.text, ''), '\n', ''), '\r', ''), '\t', '')) == ''
    return (blank & Review.rating.in_((4, 5)) & (Review.risk == 'NORMAL')
            & (Review.manual == False) & (Review.safety_critical == False))


def transient(error):
    if isinstance(error, (TimeoutError, ConnectionError, OSError, httpx.TransportError, OperationalError)):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in (408, 429) or error.response.status_code >= 500
    message = str(error).lower()
    return bool(re.search(r'http (?:408|429|5\d\d)\b', message)) or any(word in message for word in ('timeout', 'timed out', 'таймаут', 'недоступ', 'не отвечает', 'соединени', 'локальный ai', 'локальная модель'))


def business_skip(error):
    return isinstance(error, ReviewSkipped) or any(part in str(error).lower() for part in (
        'уже имеет ответ', 'уже есть ответ', 'публикация уже', 'отзыв изменился во время', 'черновик изменён во время'))


async def run_draft_batch(job_id, generate, between_reviews, session_factory=Session):
    with session_factory() as db:
        job = db.get(Job, job_id)
        initial = dict(job.result or {})
        kind = job.kind
    single = kind in ('single_draft', 'single_question_draft')
    questions = kind in ('question_drafts', 'single_question_draft')
    account = initial['account_id']
    selected = list(initial.get('selected_ids') or [])
    since = initial.get('since', '')
    processed = int(initial.get('processed', 0)) if not single else 0
    failed = int(initial.get('failed', 0)) if not single else 0
    skipped = int(initial.get('skipped', 0)) if not single else 0
    attention = int(initial.get('attention', 0)) if not single else 0
    accounted_outcomes = int(initial.get('accounted_outcomes', 0))
    accounted_attention = int(initial.get('accounted_attention', 0))
    if not single:
        with session_factory() as db:
            outcomes = list(db.scalars(select(GenerationOutcome).where(GenerationOutcome.job_id == job_id)))
        saved_attention = sum(item.attention for item in outcomes)
        processed += max(0, len(outcomes) - accounted_outcomes)
        attention += max(0, saved_attention - accounted_attention)
        accounted_outcomes, accounted_attention = len(outcomes), saved_attention
    manual_errors = int(initial.get('manual_errors', 0)) if not single else 0
    failures = list(initial.get('failures') or []) if not single else []
    excluded = set(initial.get('failed_ids') or [x['review_id'] for x in failures]) if not single else set()
    slow_ids = set(initial.get('slow_ids') or [])
    with session_factory() as db:
        policy_version = fast_policy_version(db)
    if initial.get('fast_policy_version') != policy_version:
        slow_ids.clear()
    next_policy_check = time.monotonic() + 30
    retries = dict(initial.get('retries') or {})
    active = {}
    between_task = None
    slice_started = time.monotonic()
    yielded = False
    last_draft = None
    support = inspect.signature(generate).parameters
    fast_supported = not single and not questions and 'fast_only' in support

    # Older workers counted reviews answered elsewhere as failures. Reclassify
    # only IDs whose current state proves that work is no longer needed.
    if excluded and initial.get('queue_version') != 2:
        with session_factory() as db:
            obsolete = set(db.scalars(select(Review.id).outerjoin(Draft, Draft.review_id == Review.id).where(
                Review.id.in_(excluded), or_(Review.is_answered == True, Draft.id.is_not(None)))))
        obsolete.update(item['review_id'] for item in failures if business_skip(ValueError(item.get('reason', ''))))
        obsolete_count = min(failed, len(obsolete))
        failed -= obsolete_count
        skipped += obsolete_count
        failures = [x for x in failures if x['review_id'] not in obsolete]

    def query():
        conditions = [Review.wb_account_id == account, Review.is_answered == False,
                      Review.status != 'publishing',
                      Review.marketplace.like('%_question') if questions else ~Review.marketplace.like('%_question')]
        if not single:
            conditions.append(Draft.id.is_(None))
        if selected:
            conditions.append(Review.id.in_(selected))
        if since:
            conditions.append(Review.created_at >= since)
        if excluded:
            conditions.append(Review.id.not_in(excluded))
        return select(Review).outerjoin(Draft, Draft.review_id == Review.id).where(*conditions)

    def snapshot(clear=False):
        with session_factory() as db:
            job = db.get(Job, job_id)
            remaining = db.scalar(query().with_only_columns(func.count(), maintain_column_froms=True)) or 0
            if single:
                remaining = 0 if processed else min(1, remaining)
            done = processed + failed + skipped + manual_errors
            # Each lane owns its session. Saved drafts disappear from remaining;
            # counters and totals are published together after each completed item.
            total = 1 if single else done + remaining
            running = [] if clear else [info for _, info in active.values()]
            job.result = {**(job.result or {}), 'total': total, 'processed': processed,
                          'failed': failed, 'skipped': skipped, 'attention': attention,
                          'manual_errors': manual_errors, 'failures': failures[-20:],
                          'failed_ids': sorted(excluded), 'slow_ids': sorted(slow_ids),
                          'fast_policy_version': policy_version,
                          'retries': retries, 'retrying': len(retries), 'current_reviews': running,
                          'remaining': remaining, 'updated_at': now(), 'queue_version': 2,
                          'accounted_outcomes': accounted_outcomes, 'accounted_attention': accounted_attention,
                          'message': 'Подготовка ответов продолжается на сервере'}
            job.progress = min(99, round(done / max(1, total) * 100))
            db.commit()

    def next_review(lane):
        with session_factory() as db:
            busy = [info['review_id'] for _, info in active.values()]
            waiting = [rid for rid, value in retries.items() if value['next_at'] > time.time()]
            candidate = query()
            if busy or waiting:
                candidate = candidate.where(Review.id.not_in(busy + waiting))
            if fast_supported:
                candidate = candidate.where(fast_condition(), Review.id.not_in(slow_ids)) if lane == 'fast' else candidate.where(or_(~fast_condition(), Review.id.in_(slow_ids)))
            review = db.scalar(candidate.order_by(Review.created_at.desc(), Review.id).limit(1))
            if not review:
                return None
            return {'review_id': review.id, 'rating': review.rating, 'created_at': review.created_at,
                    'started_at': now(), 'lane': lane}

    async def generate_one(info):
        token = batch_context.set({'job_id': job_id, 'requested_by': initial.get('requested_by', '')})
        try:
            return await generate_one_scoped(info)
        finally:
            batch_context.reset(token)

    async def generate_one_scoped(info):
        with session_factory() as db:
            options = {}
            if 'fast_only' in support:
                options['fast_only'] = info['lane'] == 'fast'
            if 'skip_existing' in support:
                options['skip_existing'] = not single
            draft = await generate(db, info['review_id'], initial.get('instruction', '') if single else '', **options)
            saved = db.get(Draft, draft['id'])
            if initial.get('requested_by') and saved:
                saved.created_by = saved.created_by or initial['requested_by']
                saved.updated_by = initial['requested_by']
                db.commit()
            review = db.get(Review, info['review_id'])
            return draft, bool(review and review.status == 'manual_review')

    def collect(task, info):
        nonlocal processed, failed, skipped, attention, manual_errors, last_draft, accounted_outcomes, accounted_attention
        rid = info['review_id']
        try:
            last_draft, needs_attention = task.result()
            processed += 1
            attention += int(needs_attention)
            with session_factory() as db:
                outcome = db.get(GenerationOutcome, (job_id, rid))
                if outcome:
                    accounted_outcomes += 1
                    accounted_attention += int(outcome.attention)
            retries.pop(rid, None)
        except FastPathUnavailable:
            slow_ids.add(rid)  # No model request is allowed on the fast lane.
        except GenerationBusy:
            retries[rid] = {**retries.get(rid, {}), 'next_at': time.time() + 2}
        except Exception as error:
            if single:
                raise
            if business_skip(error):
                skipped += 1
                excluded.add(rid)
                retries.pop(rid, None)
                return
            attempts = int(retries.get(rid, {}).get('attempts', 0)) + 1
            if transient(error) and attempts <= len(RETRY_DELAYS):
                retries[rid] = {'attempts': attempts, 'next_at': time.time() + RETRY_DELAYS[attempts - 1]}
                return
            category = 'technical' if transient(error) else 'manual'
            if category == 'technical':
                failed += 1
            else:
                manual_errors += 1
                attention += 1
            failures.append({'review_id': rid, 'kind': category,
                             'reason': (str(error) or type(error).__name__)[:300]})
            excluded.add(rid)
            retries.pop(rid, None)

    try:
        while True:
            if time.monotonic() >= next_policy_check:
                with session_factory() as db:
                    latest_policy = fast_policy_version(db)
                if latest_policy != policy_version:
                    policy_version = latest_policy
                    slow_ids.clear()
                next_policy_check = time.monotonic() + 30
            # Drain committed results before cancellation so saved answers retain
            # their counters even when a stop request arrives at the same moment.
            completed_any = False
            completed_model = False
            for lane, (task, info) in list(active.items()):
                if task.done():
                    del active[lane]
                    collect(task, info)
                    completed_any = True
                    completed_model = completed_model or lane == 'model'
            if between_task and between_task.done():
                between_task.result()
                between_task = None
            if completed_any:
                snapshot()
            with session_factory() as db:
                if db.get(Job, job_id).cancel_requested:
                    raise asyncio.CancelledError
            if single and processed:
                break
            if completed_any and (completed_model or 'model' not in active) and not single and between_task is None:
                between_task = asyncio.create_task(between_reviews())
            from .queue_policy import other_waiting
            with session_factory() as db:
                yield_due = not single and time.monotonic() - slice_started >= BATCH_SLICE_SECONDS and other_waiting(db, job_id)
            if yield_due and not active and between_task is None:
                yielded = True
                break
            started_any = False
            for lane in (('fast', 'model') if fast_supported else ('model',)):
                if lane not in active and (not yield_due or lane == 'fast' and 'model' in active):
                    # Imports run in their own session/task. A slow marketplace
                    # must not stop either generation lane while it awaits I/O.
                    info = next_review(lane)
                    if info:
                        active[lane] = (asyncio.create_task(generate_one(info)), info)
                        started_any = True
            if started_any:
                snapshot()
            if not active and between_task is None:
                with session_factory() as db:
                    remaining = db.scalar(query().with_only_columns(func.count(), maintain_column_froms=True)) or 0
                if not remaining:
                    break
            waiting_tasks = {task for task, _ in active.values()}
            if between_task:
                waiting_tasks.add(between_task)
            if waiting_tasks:
                await asyncio.wait(waiting_tasks, timeout=POLL_SECONDS,
                                   return_when=asyncio.FIRST_COMPLETED)
            else:
                await asyncio.sleep(POLL_SECONDS)
            if single and processed:
                break
    finally:
        if between_task and not between_task.done():
            between_task.cancel()
        if between_task:
            await asyncio.gather(between_task, return_exceptions=True)
        for task, _ in active.values():
            if not task.done():
                task.cancel()
        await asyncio.gather(*(task for task, _ in active.values()), return_exceptions=True)
        for task, info in active.values():
            if not task.cancelled():
                collect(task, info)
        active.clear()
        snapshot(clear=True)
    if single and not last_draft:
        raise ValueError('Отзыв или вопрос изменился. Обновите страницу')
    with session_factory() as db:
        result = dict(db.get(Job, job_id).result)
    return {**result, 'yielded': yielded}, last_draft


async def cancellable_batch(job_id, generate, between_reviews, session_factory=Session):
    """Observe stop requests even while a nested synchronization awaits I/O."""
    task = asyncio.create_task(run_draft_batch(job_id, generate, between_reviews, session_factory))
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=POLL_SECONDS)
            if done:
                return task.result()
            with session_factory() as db:
                stop = db.get(Job, job_id).cancel_requested
            if stop:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                raise asyncio.CancelledError
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
