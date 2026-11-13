"""Read-only recovery of write outcomes. Absence is never permission to repost.

The immutable Action item/attempt snapshot, not the possibly edited Draft, is
compared with a seller-owned comment from the same account and marketplace.
"""
import hashlib
import asyncio
import time
from datetime import datetime, timezone
from sqlalchemy import select, func, literal
from .db import Session, Job, Publication, Review, StoreProfile, Account, Action, Setting, setting, set_setting, now
from .publication_errors import PublicationError

KIND = 'publication_reconcile'
PENDING = ('in_flight', 'uncertain', 'answer_pending')


def key(publication):
    return 'publication-attempt:' + publication.id


def account_identity(db, store):
    account = db.get(Account, store.id)
    # Never reuse evidence after replacing an account's connection/credentials.
    token = account.encrypted_token if account else ''
    return {'provider': store.provider, 'store_id': store.id,
            'business_id': str((store.config or {}).get('business_id', '')),
            'client_id': str((store.config or {}).get('client_id', '')),
            'connection': hashlib.sha256(str(token).encode()).hexdigest()}


def prepare_attempt(db, publication, review, item):
    previous = setting(db, key(publication), {})
    store = db.get(StoreProfile, review.wb_account_id)
    if not store or store.provider != review.marketplace.removesuffix('_question'):
        raise ValueError('Маркетплейс обращения не совпадает с магазином.')
    set_setting(db, key(publication), {'identity': account_identity(db, store),
        'review_id': review.id, 'external_id': review.wb_review_id,
        'marketplace': review.marketplace, 'text': item['text'],
        'revision': item['revision'], 'draft_id': item['draft_id'],
        'action_id': publication.action_id, 'attempted_at': now(),
        'attempt_number': int(previous.get('attempt_number', 0)) + 1,
        'previous_action_ids': list(previous.get('previous_action_ids', [])) +
            ([previous['action_id']] if previous.get('action_id') else [])})


def can_retry(db, publication, review):
    if not publication or publication.status != 'retryable':
        return False
    state = setting(db, key(publication), {})
    store = db.get(StoreProfile, review.wb_account_id)
    return bool(store and state.get('identity') == account_identity(db, store) and
        not state.get('legacy_identity_unknown') and
        state.get('review_id') == review.id and state.get('external_id') == review.wb_review_id and
        state.get('outcome') in ('not_sent', 'rejected') and state.get('retryable') is True and
        float(state.get('retry_at', 0)) <= time.time())


def schedule_reconciliation(db, publication, review, *, delay=30):
    jid = KIND + ':' + review.wb_account_id
    job = db.get(Job, jid)
    if job:
        # One store has one durable reader, regardless of publication volume.
        # Explicit cancellation remains respected.
        if job.cancel_requested or job.status == 'cancelled':
            return
        if job.status in ('completed', 'failed'):
            job.status, job.progress = 'queued', 0
        job.result = {**(job.result or {}), 'retry_at': min(
            float((job.result or {}).get('retry_at', time.time() + delay)), time.time() + delay)}
        return
    db.add(Job(id=jid, kind=KIND, priority=170, max_attempts=3, result={
        'account_id': review.wb_account_id,
        'retry_at': time.time() + delay, 'processed': 0,
        'message': 'Сверяем результат отправки с маркетплейсом'}))


def record_outcome(db, publication, review, result=None, error=None):
    state = dict(setting(db, key(publication), {}) or {})
    if error is not None:
        outcome = error.outcome if isinstance(error, PublicationError) else 'uncertain'
        retryable = isinstance(error, PublicationError) and error.retryable
        # A safe retry remains subject to a new Action and all current checks.
        status = 'retryable' if retryable and outcome in ('not_sent', 'rejected') else (
            'rejected' if outcome in ('not_sent', 'rejected') else 'uncertain')
        state.update(outcome=outcome, retryable=retryable,
            retry_at=time.time() + max(getattr(error, 'retry_after', 60),
                min(3600, 30 * 2 ** min(int(state.get('attempt_number', 1)), 7))),
            last_error=str(error)[:500])
    else:
        result = result or {'state': 'published'}  # Providers with synchronous confirmation.
        if not isinstance(result, dict):
            result = {'state': 'uncertain'}
        status = result.get('state', 'uncertain')
        status = 'answer_pending' if status in ('accepted', 'answer_pending') else status
        if status not in ('published', 'answer_pending', 'rejected', 'mismatch'):
            status = 'uncertain'
        state.update(outcome=status, comment_id=result.get('comment_id', ''), retryable=False)
    publication.status = status
    if status == 'published':
        publication.confirmed_at = publication.confirmed_at or now()
        review.status, review.is_answered = 'published', True
        review.existing_answer = state.get('text', review.existing_answer)
    elif status == 'answer_pending':
        review.status = 'answer_pending'
    elif status == 'retryable':
        review.status = 'manual_review' if review.manual else 'draft_ready'
    else:
        review.status = 'manual_review'
    state['updated_at'] = now()
    set_setting(db, key(publication), state)
    if status in PENDING:
        schedule_reconciliation(db, publication, review)
    return status


def wb_evidence(response, external_id, expected_text):
    data = response.get('data') if isinstance(response, dict) else None
    if response.get('error') or not isinstance(data, dict) or str(data.get('id')) != str(external_id):
        raise ValueError('WB не подтвердил выбранный отзыв.')
    answer = data.get('answer') or {}
    if not answer:
        return {'state': 'absent'}
    if not isinstance(answer, dict) or not isinstance(answer.get('text'), str):
        raise ValueError('WB не вернул текст ответа.')
    if answer['text'].strip() != expected_text.strip():
        return {'state': 'mismatch'}
    if answer.get('state') in ('rejected', 'banned'):
        return {'state': 'rejected'}
    return {'state': 'published', 'text': answer['text']}


def yandex_comment_evidence(comment, external_id, expected_text):
    # BUSINESS alone also includes another business. canModify proves ownership.
    if (not isinstance(comment, dict) or comment.get('canModify') is not True or
        (comment.get('author') or {}).get('type') != 'BUSINESS' or comment.get('parentId')):
        return None
    if str(comment.get('feedbackId')) != str(external_id):
        raise ValueError('Яндекс Маркет вернул комментарий другого отзыва.')
    if str(comment.get('text', '')).strip() != expected_text.strip():
        return {'state': 'mismatch'}
    states = {'PUBLISHED': 'published', 'UNMODERATED': 'answer_pending',
              'BANNED': 'rejected', 'DELETED': 'rejected'}
    return {'state': states.get(comment.get('status'), 'unknown'),
            'comment_id': str(comment.get('id', '')), 'text': comment.get('text', '')}


async def remote_evidence(db, review, state):
    from .integrations import store_credentials, wb_token, YandexProvider, OzonProvider
    from .security import guard
    store = db.get(StoreProfile, review.wb_account_id)
    if not store or state.get('identity') != account_identity(db, store):
        raise ValueError('Подключение магазина изменилось после отправки; нужна проверка аккаунта.')
    if review.marketplace.endswith('_question'):
        # Review comment APIs cannot prove the outcome of a question reply.
        raise ValueError('Для этого ответа требуется сверка в кабинете вопросов.')
    external_id, expected = state['external_id'], state['text']
    if store.provider == 'wb':
        value = await guard.request('wb', 'review', token=wb_token(db, store.id), params={'id': external_id})
        return wb_evidence(value, external_id, expected)
    _, token = store_credentials(db, store.id)
    if store.provider == 'ozon':
        from .ozon_reviews import seller_reply
        evidence = await seller_reply(OzonProvider().headers(store, token), external_id,
                                      expected_text=expected, comment_id=state.get('comment_id'))
        if evidence.get('state') == 'answer_rejected':
            evidence = {**evidence, 'state': 'rejected'}
        return evidence
    if store.provider == 'yandex':
        if not str(external_id).isdigit():
            raise ValueError('Некорректный идентификатор отзыва Яндекс Маркета.')
        page, seen, mismatch = '', set(), False
        for _ in range(100):
            params = {'limit': 50}
            if page:
                params['pageToken'] = page
            value = await guard.request('yandex', 'review_comments', headers={'Api-Key': token},
                url=YandexProvider().target(store, 'review_comments'), params=params,
                body={'feedbackId': int(external_id)})
            payload = value.get('result') or {}
            if value.get('status') != 'OK' or not isinstance(payload.get('comments'), list):
                raise ValueError('Яндекс Маркет не вернул подтверждённый список комментариев.')
            for comment in payload['comments']:
                evidence = yandex_comment_evidence(comment, external_id, expected)
                if evidence and evidence['state'] != 'mismatch':
                    return evidence
                mismatch = mismatch or bool(evidence)
            page = str((payload.get('paging') or {}).get('nextPageToken') or '')
            if not page:
                return {'state': 'mismatch' if mismatch else 'absent'}
            if page in seen:
                break
            seen.add(page)
        raise ValueError('Сверка комментариев Яндекс Маркета не завершена.')
    raise ValueError('Маркетплейс не поддерживает сверку.')


def attempt_state(db, publication, review):
    state = dict(setting(db, key(publication), {}) or {})
    if not state:
        # Backfill interrupted older versions from the frozen confirmation, never
        # from a current draft. Bind to the review's persisted store, not the UI.
        action = db.get(Action, publication.action_id)
        item = next((item for item in (action.payload or {}).get('items', [])
            if item.get('review_id') == review.id), None) if action else None
        if not item or not item.get('text'):
            return {}
        prepare_attempt(db, publication, review, item)
        state = dict(setting(db, key(publication), {}) or {})
        state['attempted_at'] = action.created_at
        state['legacy_identity_unknown'] = True
        set_setting(db, key(publication), state)
    return state


def enqueue_due(db):
    """Restart recovery: discover durable reservations without mutating them."""
    added = 0
    stores = db.scalars(select(Review.wb_account_id).join(Publication, Publication.review_id == Review.id)
        .where(Publication.status.in_(PENDING)).distinct()).all()
    for store_id in stores:
        job = db.get(Job, KIND + ':' + store_id)
        if job and (job.cancel_requested or job.status in ('queued', 'running', 'cancelled')):
            continue
        row = db.execute(select(Publication, Review).join(Review, Review.id == Publication.review_id)
            .where(Publication.status.in_(PENDING), Review.wb_account_id == store_id).limit(1)).one()
        schedule_reconciliation(db, *row, delay=0)
        added += 1
    return added


async def run_step(job_id, session_factory=Session):
    """One bounded store slice. Retry delays live on each publication snapshot."""
    started = time.monotonic()
    for _ in range(20):
        processed = await _reconcile_one(job_id, session_factory)
        if not processed or time.monotonic() - started >= 30:
            return


async def _reconcile_one(job_id, session_factory):
    with session_factory() as db:
        job = db.get(Job, job_id)
        if not job or job.kind != KIND or job.cancel_requested or job.status not in ('queued', 'running'):
            return False
        if float((job.result or {}).get('retry_at', 0)) > time.time():
            return False
        pending = select(Publication, Review).join(Review, Review.id == Publication.review_id).outerjoin(
            Setting, Setting.key == literal('publication-attempt:') + Publication.id).where(
            Publication.status.in_(PENDING), Review.wb_account_id == (job.result or {}).get('account_id'))
        due = func.coalesce(Setting.value['reconcile_after'].as_float(), 0)
        row = db.execute(pending.where(due <= time.time()).order_by(due, Publication.id).limit(1)).first()
        if not row:
            remaining = db.execute(pending.order_by(due, Publication.id).limit(1)).first()
            if remaining:
                state = setting(db, key(remaining[0]), {}) or {}
                job.status = 'queued'
                job.result = {**job.result, 'retry_at': max(time.time() + 1, float(state.get('reconcile_after', 0))),
                    'last_served_at': now(), 'message': 'Ожидаем подтверждения маркетплейса.'}
            else:
                job.status, job.progress = 'completed', 100
                job.result = {**job.result, 'message': 'Сверка отправок завершена.', 'finished_at': now()}
            db.commit(); return False
        publication, review = row
        state = attempt_state(db, publication, review)
        if (not state or state.get('review_id') != review.id or state.get('external_id') != review.wb_review_id or
            state.get('marketplace') != review.marketplace or (job.result or {}).get('account_id') != review.wb_account_id):
            publication.status = 'mismatch'
            job.result = {**job.result, 'last_error': 'Нет достоверного снимка отправки; сохранённые тексты не изменены.'}
            db.commit(); return True
        try:
            age = time.time() - datetime.fromisoformat(state['attempted_at']).timestamp()
        except (KeyError, ValueError, TypeError):
            age = 600
        if publication.status == 'in_flight' and age < 180:
            set_setting(db, key(publication), {**state, 'reconcile_after': time.time() + 180 - age})
            db.commit(); return True
        expected_action = publication.action_id
        db.commit()  # No database transaction is held across marketplace I/O.
        error = ''
        try:
            evidence = await asyncio.wait_for(remote_evidence(db, review, state), timeout=20)
        except (ValueError, TimeoutError) as failure:
            evidence, error = {'state': 'unknown'}, str(failure)[:500]
        db.expire_all()
        publication = db.scalar(select(Publication).where(Publication.id == publication.id).with_for_update())
        job, review = db.get(Job, job_id), db.get(Review, review.id)
        if (job.cancel_requested or job.status not in ('queued', 'running') or
            publication.action_id != expected_action or publication.status not in PENDING):
            db.rollback(); return False
        store = db.get(StoreProfile, review.wb_account_id)
        if not store or state.get('identity') != account_identity(db, store):
            publication.status = 'mismatch'
            job.result = {**job.result, 'last_error': 'Подключение магазина изменилось; требуется сверка аккаунта.'}
            db.commit(); return True
        remote_state = evidence.get('state')
        if remote_state in ('published', 'rejected', 'mismatch'):
            record_outcome(db, publication, review, evidence)
            message = {'published': 'Отправка подтверждена маркетплейсом.',
                'rejected': 'Маркетплейс отклонил ответ. Текст сохранён, повтор не выполняется.',
                'mismatch': 'В кабинете обнаружен другой ответ. Сохранённый черновик не изменён.'}[remote_state]
        else:
            # Even a complete empty remote list can be eventually consistent.
            # Continue read-only reconciliation, never reset the reservation.
            publication.status = 'answer_pending' if remote_state == 'answer_pending' else 'uncertain'
            review.status = 'answer_pending' if remote_state == 'answer_pending' else 'manual_review'
            message = 'Ответ проходит модерацию.' if remote_state == 'answer_pending' else 'Ожидаем подтверждения отправки от маркетплейса.'
        current_state = dict(setting(db, key(publication), {}) or {})
        checks = int(current_state.get('reconcile_checks', 0)) + 1
        set_setting(db, key(publication), {**current_state, 'reconcile_checks': checks,
            'reconcile_after': time.time() + min(21600, 30 * 2 ** min(checks, 10)),
            'remote_state': remote_state, 'last_reconcile_error': error, 'reconciled_at': now()})
        job.status = 'queued'
        job.result = {**job.result, 'processed': int((job.result or {}).get('processed', 0)) + 1,
            'updated_at': now(), 'last_served_at': now(), 'retry_at': 0,
            'message': message, 'last_error': error, 'remote_state': remote_state,
            'last_publication_id': publication.id}
        db.commit()
        from .read_cache import clear_reads
        clear_reads()
        return True
