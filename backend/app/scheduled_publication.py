"""Durable, explicitly enabled publication of verified saved replies. No generation."""
import time
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import select, func, or_, and_
from .db import Session, Setting, Job, User, StoreProfile, Account, Review, Draft, Product, Publication, Action, Activity, setting, now

KIND = 'scheduled_publication'
PROVIDERS = ('wb', 'ozon', 'yandex')
BATCH_SIZE = 200
FIELDS = ('publication_enabled', 'publication_hours', 'publication_reviews', 'publication_questions', 'publication_low_ratings', 'publication_all_low_ratings')


def market_enabled(schedule, provider):
    return (schedule.get('publication_markets') or {}).get(provider, True)


def availability_reason(db):
    from .config import REAL_PUBLISH
    if not REAL_PUBLISH or setting(db, 'test_mode', True) or setting(db, 'safe_mode', False):
        return 'Для автоматической отправки нужен рабочий режим с разрешённой публикацией.'
    if not setting(db, 'assistant_enabled', True):
        return 'Ассистент выключен.'
    return ''


def blocked_reason(db, schedule):
    reason = availability_reason(db)
    if reason:
        return reason
    actor = db.get(User, schedule.get('publication_authorized_by', ''))
    if not actor or not actor.enabled or actor.role != 'owner':
        return 'Владелец должен включить и сохранить автоматическую выгрузку.'
    return ''


def authorized_job(db, job_id, *, lock=False):
    statement = select(Setting).where(Setting.key == 'automation_schedule')
    row = db.scalar(statement.with_for_update() if lock else statement)
    schedule = dict(row.value or {}) if row else {}
    job = db.get(Job, job_id, populate_existing=True)
    if (not job or job.kind != KIND or job.cancel_requested or job.status not in ('queued', 'running')
        or not schedule.get('enabled') or not schedule.get('publication_enabled')
        or not schedule.get('publication_authorization')
        or schedule.get('publication_authorization') != (job.result or {}).get('authorization')):
        raise ValueError('Автоматическая выгрузка остановлена или расписание изменилось.')
    reason = blocked_reason(db, schedule)
    store_id = (job.result or {}).get('account_id')
    store = db.get(StoreProfile, store_id)
    if reason or not store or store.provider not in PROVIDERS or not db.get(Account, store_id):
        raise ValueError(reason or 'Магазин больше не подключён.')
    if not market_enabled(schedule, store.provider):
        raise ValueError('Автоматическая выгрузка для этого маркетплейса выключена.')
    return schedule, job


def candidate_query(store_id, schedule, cursor=None):
    question = Review.marketplace.like('%_question')
    kinds = []
    if schedule.get('publication_reviews', True):
        kinds.append(and_(~question, Review.rating >= 4))
    if schedule.get('publication_low_ratings', False):
        kinds.append(and_(~question, Review.rating.between(1, 3)))
    if schedule.get('publication_questions', True):
        kinds.append(question)
    query = select(Review).join(Draft, Draft.review_id == Review.id).where(
        Review.wb_account_id == store_id, Review.is_answered == False, or_(Review.status == 'draft_ready', and_(
            Review.status == 'manual_review', Review.rating >= 4, ~question, Draft.quality_technical == True), and_(
            Review.status == 'manual_review', Review.rating.between(1, 3), ~question,
            bool(schedule.get('publication_low_ratings', False)))),
        Review.manual == False, Review.safety_critical == False, Review.risk == 'NORMAL',
        or_(and_(Draft.quality_passed == True, Draft.quality_pending == False, Draft.quality_technical == False,
                 Draft.quality_attention == False),
            and_(Review.rating >= 4, ~question, Draft.quality_technical == True)),
        or_(*kinds) if kinds else False,
        ~select(Publication.id).where(Publication.review_id == Review.id,
            Publication.status != 'retryable').correlate(Review).exists())
    if cursor:
        query = query.where(or_(Review.created_at < cursor[0],
            and_(Review.created_at == cursor[0], Review.id < cursor[1])))
    return query.order_by(Review.created_at.desc(), Review.id.desc())


def recover_positive_verdict(db, review, draft):
    """Fresh local verification of unchanged saved praise, without generation.

    Runs before eligibility, never grants publication authorization. All normal
    schedule, revision, provider and duplicate-publication guards still run.
    """
    from .rule_retry import technical_check
    from .rule_execution import verified_positive_reply
    from .services import assess_reply, review_verified_facts, matching_owner_rules
    from .owner_reply_policy import saved_rule_quality, attach_rule_quality
    if not review or not draft or not technical_check(draft.quality):
        return False
    review = db.scalar(select(Review).where(Review.id == review.id).with_for_update().execution_options(populate_existing=True))
    draft = db.scalar(select(Draft).where(Draft.id == draft.id).with_for_update().execution_options(populate_existing=True))
    if (review.is_answered or review.status not in ('draft_ready', 'manual_review') or
        db.scalar(select(Publication.id).where(Publication.review_id == review.id))):
        return False
    product = db.get(Product, review.product_id)
    rules = matching_owner_rules(db, product)
    if not verified_positive_reply(draft.text, review, rules, product=product):
        return False
    facts = review_verified_facts(db, review, product, rules)
    quality = assess_reply(review, product, draft.text, facts)
    verdict = saved_rule_quality(draft.text, rules, review=review)
    if not quality['passed'] or not verdict['passed']:
        return False
    draft.quality = attach_rule_quality({**(draft.quality or {}), **quality,
                                      'text_issues': list(quality.get('issues', []))}, verdict)
    review.status = 'draft_ready'
    from .learned_reply_templates import remember_template
    remember_template(db, review, product, rules, facts, draft)
    db.flush()
    return True


def eligibility_reason(db, review, draft, schedule):
    from .services import matching_owner_rules, saved_rule_quality, validate_reply
    low = bool(review and not review.marketplace.endswith('_question') and 1 <= review.rating <= 3)
    if not review or not draft or review.is_answered or review.status not in (('draft_ready', 'manual_review') if low else ('draft_ready',)):
        return 'Ответ больше не готов к отправке.'
    from .publication_constraints import publication_restriction
    restriction = publication_restriction(review)
    if restriction:
        return restriction
    question = review.marketplace.endswith('_question')
    provider = review.marketplace.removesuffix('_question')
    store = db.get(StoreProfile, review.wb_account_id)
    if not store or provider != store.provider or provider not in PROVIDERS:
        return 'Маркетплейс обращения не совпадает с магазином.'
    if (review.manual or review.safety_critical or review.risk != 'NORMAL' or
        not question and (not schedule.get('publication_low_ratings', False) if low else not schedule.get('publication_reviews', True)) or
        question and not schedule.get('publication_questions', True)):
        return 'Нужна личная проверка или этот тип ответов выключен.'
    if low and not schedule.get('publication_all_low_ratings', False):
        from .low_rating_templates import approved_template
        if not approved_template(db, review, draft):
            return 'Для низкой оценки нет подходящего проверенного шаблона. Нужна личная проверка.'
    publication = db.scalar(select(Publication).where(Publication.review_id == review.id))
    if publication:
        from .publication_reconciliation import can_retry
        if not can_retry(db, publication, review):
            return 'Публикация уже выполнялась или ожидает безопасного повтора.'
    quality = draft.quality or {}
    if quality.get('passed') is not True or quality.get('text_issues'):
        return 'Сначала завершите проверку текста.'
    if quality.get('owner_rules') is not None and not isinstance(quality['owner_rules'], dict):
        return 'Сначала завершите проверку правил.'
    previous = quality.get('owner_rules') or {}
    if (previous.get('technical_error') or previous.get('checked') is False or
        previous.get('status') in ('error', 'pending') or previous.get('violations') or
        previous and previous.get('passed') is not True):
        return 'Сначала завершите проверку правил.'
    rules = matching_owner_rules(db, db.get(Product, review.product_id), for_questions=question)
    if not saved_rule_quality(draft.text, rules, quality.get('owner_rules'), review=review)['passed']:
        return 'Сначала проверьте ответ по актуальным правилам.'
    if question and quality.get('fitment', {}).get('status') in ('NEEDS_CATALOG', 'CONFLICT'):
        return 'Нужна проверка применяемости по каталогу.'
    try:
        if validate_reply(draft.text) != draft.text or provider == 'yandex' and len(draft.text) > 4096:
            return 'Текст требует исправления перед отправкой.'
        if not question:
            from .reply_policy import semantic_reply_issues
            if semantic_reply_issues(review, db.get(Product, review.product_id), draft.text):
                return 'Ответ не соответствует содержанию отзыва.'
            from .services import assess_reply, trusted_product_facts
            product = db.get(Product, review.product_id)
            if not assess_reply(review, product, draft.text, trusted_product_facts(db, product))['passed']:
                return 'Текст не прошёл актуальную оценку качества.'
    except ValueError:
        return 'Текст требует проверки.'
    return ''


def enqueue(db, schedule, store, current):
    if not schedule.get('publication_enabled') or not market_enabled(schedule, store.provider) or blocked_reason(db, schedule):
        return False
    last_key = f'last_publication:{store.id}'
    if current - float(schedule.get(last_key, 0)) < int(schedule.get('publication_hours', 1)) * 3600:
        return False
    if db.scalar(select(Job.id).where(Job.kind == KIND, Job.status.in_(('queued', 'running')),
        Job.cancel_requested == False, Job.result['account_id'].as_string() == store.id).limit(1)):
        return False
    db.add(Job(id=str(uuid4()), kind=KIND, priority=180, max_attempts=3, result={
        'account_id': store.id, 'scheduled': True, 'authorization': schedule.get('publication_authorization'),
        'requested_by': schedule.get('publication_authorized_by'), 'cursor': None,
        'scanned': 0, 'skipped': 0, 'published': 0, 'uncertain': 0,
        'message': 'Плановая выгрузка готовых ответов поставлена в очередь'}))
    schedule[last_key] = current
    return True


def publication_counts(db, job):
    rows = db.execute(select(Publication.status, func.count())
        .where(Publication.scheduled_job_id == job.id).group_by(Publication.status)).all()
    counts = dict(rows)
    return {'published': counts.get('published', 0),
        'uncertain': sum(counts.get(key, 0) for key in ('in_flight', 'uncertain', 'mismatch')),
        'moderation': counts.get('answer_pending', 0), 'retryable': counts.get('retryable', 0),
        'rejected': counts.get('rejected', 0)}


def refresh_counts(db, job):
    job.result = {**(job.result or {}), **publication_counts(db, job)}


def finish(db, job, schedule, message):
    refresh_counts(db, job)
    job.status, job.progress = 'completed', 100
    state = dict(job.result or {})
    if not state.get('completion_logged'):
        actor = db.get(User, schedule['publication_authorized_by'])
        db.add(Activity(user_id=schedule['publication_authorized_by'], username=actor.username if actor else 'automation',
            method='POST', path='/jobs/scheduled-publication', status=200,
            details={'event': 'scheduled_publication_completed', 'store_id': state['account_id'],
                'published': state['published'], 'uncertain': state['uncertain']}))
    job.result = {**state, 'completion_logged': True, 'finished_at': now(), 'updated_at': now(), 'message': message}
    db.commit()


async def run_step(job_id, session_factory=Session):
    from .services import confirm_action
    started = time.monotonic()
    for index in range(BATCH_SIZE):
        with session_factory() as db:
            try:
                schedule, job = authorized_job(db, job_id)
            except ValueError as error:
                job = db.get(Job, job_id)
                if job and job.status in ('queued', 'running'):
                    job.status = 'cancelled'
                    job.result = {**(job.result or {}), 'message': str(error), 'finished_at': now()}
                    db.commit()
                return
            if index == 0:
                refresh_counts(db, job)
            state = dict(job.result or {})
            # Each ambiguous attempt has a durable read-only reconciliation.
            # Its unique reservation blocks that review, not unrelated replies.
            from .publication_reconciliation import enqueue_due
            enqueue_due(db)
            review = db.scalar(candidate_query(state['account_id'], schedule, state.get('cursor')).limit(1))
            if not review:
                finish(db, job, schedule, 'Выгрузка завершена. Новые готовые ответы попадут в следующий запуск.')
                return
            cursor = [review.created_at, review.id]
            review_id = review.id
            draft = db.scalar(select(Draft).where(Draft.review_id == review.id))
            recover_positive_verdict(db, review, draft)
            reason = eligibility_reason(db, review, draft, schedule)
            if reason and ('правил' in reason or 'проверку текста' in reason):
                from .rule_change_recheck import enqueue_draft
                enqueue_draft(db, draft, review, actor_id=schedule.get('publication_authorized_by', ''))
            aid = None
            if not reason:
                action = Action(id=str(uuid4()), kind='publish', payload={
                    'scheduled_job_id': job_id, 'items': [{'review_id': review.id, 'draft_id': draft.id,
                        'revision': draft.revision, 'text': draft.text, 'rating': review.rating,
                        'manual': False, 'kind': 'question' if review.marketplace.endswith('_question') else 'review'}],
                    'excluded': [], 'test_mode': False})
                db.add(action); db.commit(); aid = action.id
                try:
                    await confirm_action(db, aid, actor_id=schedule['publication_authorized_by'], scheduled_job_id=job_id)
                except ValueError as error:
                    db.rollback()
                    action = db.get(Action, aid)
                    if action and action.status == 'pending':
                        action.status = 'cancelled'; db.commit()
                    reason = str(error)
            db.expire_all()
            job = db.get(Job, job_id)
            outcome = db.scalar(select(Publication.status).where(Publication.review_id == review_id,
                Publication.scheduled_job_id == job_id)) if aid else None
            job.result = {**job.result, 'cursor': cursor, 'scanned': int((job.result or {}).get('scanned', 0)) + 1,
                'processed': int((job.result or {}).get('scanned', 0)) + 1,
                'published': int((job.result or {}).get('published', 0)) + int(outcome == 'published'),
                'uncertain': int((job.result or {}).get('uncertain', 0)) + int(outcome in ('in_flight', 'uncertain', 'mismatch')),
                'skipped': int((job.result or {}).get('skipped', 0)) + int(bool(reason)),
                'last_reason': reason, 'updated_at': now(), 'last_served_at': now(),
                'message': 'Публикуем готовые ответы на сервере'}
            job.status = 'cancelled' if job.cancel_requested else 'queued'
            db.commit()
            if job.status in ('completed', 'cancelled') or time.monotonic() - started >= 15:
                return


def runtime(db, schedule):
    reason = blocked_reason(db, schedule) if schedule.get('publication_enabled') else ''
    stores = []
    for store in db.scalars(select(StoreProfile).where(StoreProfile.provider.in_(PROVIDERS)).order_by(StoreProfile.created_at)):
        connected = db.get(Account, store.id) is not None
        job = db.scalar(select(Job).where(Job.kind == KIND, Job.result['account_id'].as_string() == store.id)
            .order_by(Job.created_at.desc()).limit(1))
        state = {**(job.result or {}), **publication_counts(db, job)} if job else {}
        pending = job and job.status in ('queued', 'running') and not job.cancel_requested
        selected = market_enabled(schedule, store.provider)
        last = float(schedule.get(f'last_publication:{store.id}', 0))
        due = max(time.time(), last + int(schedule.get('publication_hours', 1)) * 3600)
        stores.append({'id': store.id, 'name': store.name, 'provider': store.provider, 'connected': connected, 'enabled': selected,
            'status': job.status if job else 'idle', 'job_id': job.id if job else None,
            'scanned': state.get('scanned', 0), 'published': state.get('published', 0),
            'skipped': state.get('skipped', 0), 'uncertain': state.get('uncertain', 0),
            'message': state.get('message', ''), 'last_reason': state.get('last_reason', ''), 'finished_at': state.get('finished_at'),
            'updated_at': state.get('updated_at'), 'next_at': datetime.fromtimestamp(due, timezone.utc).isoformat()
            if connected and selected and schedule.get('publication_enabled') and not reason and not pending else None})
    return {'available': not availability_reason(db), 'availability_reason': availability_reason(db), 'blocked_reason': reason, 'stores': stores}


