"""Frozen, store-scoped WB manifests; only explicit confirmation starts writes.

Manifests reuse Action storage in bounded chunks. Each network operation reuses
the existing publication reservation, revision/rule checks and uncertainty guard.
"""
import time
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from sqlalchemy import select
from .db import Session, Action, Job, Review, Draft, Product, Publication, StoreProfile, User, setting, now
from .owner_reply_rules import matching_owner_rules
from .owner_reply_policy import saved_rule_quality, fingerprint
from .reply_policy import semantic_reply_issues
from .safety import validate_reply

PREPARE_KIND = 'wb_publish_prepare'
SEND_KIND = 'wb_publish_send'
PARENT_KIND = 'wb_publish_all'
MANIFEST_KIND = 'wb_publish_manifest'
PAGE_SIZE = 100
SLICE_SECONDS = 30


async def publication_pause():
    # Do not burst an entire prepared page into the marketplace API.
    await asyncio.sleep(1)


def parent_for(db, aid, store_id):
    parent = db.get(Action, aid)
    if not parent or parent.kind != PARENT_KIND or parent.payload.get('account_id') != store_id:
        return None
    return parent


def start(db, store_id, actor_id, include_excluded=False):
    # Serialize double clicks across API processes, without a table-wide lock.
    store = db.scalar(select(StoreProfile).where(StoreProfile.id == store_id).with_for_update())
    if not store or store.provider != 'wb':
        raise ValueError('Массовая отправка доступна только для Wildberries')
    actor = db.get(User, actor_id)
    if include_excluded and (not actor or not actor.enabled or actor.role != 'owner'):
        raise ValueError('Включить исключённые ответы может только владелец')
    existing = db.scalar(select(Action).where(Action.kind == PARENT_KIND,
        Action.status.in_(('preparing', 'pending', 'sending')),
        Action.payload['account_id'].as_string() == store_id).order_by(Action.created_at.desc()))
    if existing:
        if bool(existing.payload.get('include_excluded')) != include_excluded:
            raise ValueError('Сначала отмените текущий пакет, чтобы изменить состав')
        return existing
    aid, jid = str(uuid4()), str(uuid4())
    parent = Action(id=aid, kind=PARENT_KIND, status='preparing', payload={
        'account_id': store_id, 'total': 0, 'scanned': 0, 'excluded': 0,
        'include_excluded': include_excluded, 'included_excluded': 0,
        'prepare_job_id': jid, 'requested_by': actor_id}, result={})
    db.add_all([parent, Job(id=jid, kind=PREPARE_KIND, result={
        'account_id': store_id, 'parent_id': aid, 'requested_by': actor_id, 'cursor': ''})])
    db.commit()
    return parent


def view(db, parent):
    from .config import REAL_PUBLISH
    payload, result = parent.payload, parent.result or {}
    job_id = payload.get('send_job_id') if parent.status == 'sending' else payload.get('prepare_job_id')
    job = db.get(Job, job_id) if job_id else None
    return {'id': parent.id, 'status': parent.status, 'store_id': payload['account_id'],
        **{key: payload.get(key, 0) for key in ('total', 'scanned', 'excluded')},
        **{key: result.get(key, 0) for key in ('processed', 'published', 'skipped', 'uncertain')},
        'message': result.get('message', ''),
        'job_status': job.status if job else '',
        'job_error': (job.result or {}).get('detail', '') if job and job.status in ('failed', 'interrupted') else '',
        'blocked': bool(setting(db, 'safe_mode', False) or setting(db, 'test_mode', True) or not REAL_PUBLISH),
        'prepared_at': payload.get('prepared_at'),
        'include_excluded': bool(payload.get('include_excluded')),
        'included_excluded': payload.get('included_excluded', 0)}


def preview(db, parent, offset=0):
    # Pages are independent of the review list and remain frozen after edits.
    rows = db.scalars(select(Action).where(Action.kind == MANIFEST_KIND,
        Action.payload['parent_id'].as_string() == parent.id,
        Action.payload['end_index'].as_integer() > offset,
        Action.payload['start_index'].as_integer() < offset + 20
        ).order_by(Action.payload['start_index'].as_integer()))
    items = []
    for row in rows:
        position = row.payload['start_index']
        for item in row.payload['items']:
            if position >= offset and len(items) < 20:
                items.append(item)
            position += 1
        if len(items) == 20:
            break
    return {'items': items, 'offset': offset, 'total': parent.payload['total']}


def confirm(db, parent, actor_id, real_publish, excluded_ack=False):
    db.rollback()
    parent = db.scalar(select(Action).where(Action.id == parent.id).with_for_update().execution_options(populate_existing=True))
    if parent.status == 'sending':
        return parent  # A repeated confirmation cannot create another sender.
    if parent.status != 'pending' or not parent.payload.get('total'):
        raise ValueError('Сначала дождитесь подготовки списка ответов')
    if parent.payload.get('include_excluded'):
        actor = db.get(User, actor_id)
        if not actor or not actor.enabled or actor.role != 'owner' or not excluded_ack:
            raise ValueError('Владелец должен отдельно подтвердить ответы без автоматического одобрения')
    if datetime.now(timezone.utc) - datetime.fromisoformat(parent.payload['prepared_at']) > timedelta(minutes=30):
        raise ValueError('Список устарел. Отмените его и подготовьте новый')
    if setting(db, 'safe_mode', False) or setting(db, 'test_mode', True) or not real_publish:
        raise ValueError('Реальная отправка заблокирована. Проверьте рабочий режим и разрешение публикации')
    jid = str(uuid4())
    parent.status = 'sending'
    parent.payload = {**parent.payload, 'send_job_id': jid}
    parent.result = {'confirmed_by': actor_id, 'confirmed_at': now(), 'processed': 0,
                     'published': 0, 'skipped': 0, 'uncertain': 0,
                     'excluded_ack': bool(parent.payload.get('include_excluded') and excluded_ack)}
    db.add(Job(id=jid, kind=SEND_KIND, result={'account_id': parent.payload['account_id'],
        'parent_id': parent.id, 'requested_by': actor_id, 'total': parent.payload['total']}))
    db.commit()
    return parent


def cancel(db, parent):
    parent = db.scalar(select(Action).where(Action.id == parent.id).with_for_update().execution_options(populate_existing=True))
    if parent.status not in ('preparing', 'pending', 'sending'):
        raise ValueError('Пакет уже завершён')
    parent.status = 'cancelled'
    for key in ('prepare_job_id', 'send_job_id'):
        job = db.get(Job, parent.payload.get(key, ''))
        if job and job.status in ('queued', 'running'):
            job.cancel_requested = True
    parent.result = {**(parent.result or {}), 'message': 'Отправка остановлена. Уже начатый запрос может завершиться.'}
    db.commit()


def eligible(db, review, draft, rules):
    return not exclusion_reason(db, review, draft, rules)


def exclusion_reason(db, review, draft, rules):
    if review.marketplace != 'wb' or review.is_answered:
        return 'not_ready'
    if review.rating < 4:
        return 'low_rating'
    if review.manual or review.safety_critical or review.risk != 'NORMAL':
        return 'safety'
    previous = (draft.quality or {}).get('owner_rules') or {}
    if previous.get('technical_error'):
        return 'check_timeout' if 'время' in previous.get('reason', '') else 'check_error'
    if previous and previous.get('checked') is False:
        return 'rules_unchecked'
    if previous and previous.get('passed') is not True:
        return 'rules_violation'
    if rules and previous.get('fingerprint') != fingerprint(rules):
        return 'rules_changed'
    if review.status != 'draft_ready':
        return 'not_ready'
    if not (draft.quality or {}).get('passed'):
        return 'text_quality'
    try:
        if not saved_rule_quality(draft.text, rules, previous, review=review)['passed']:
            return 'rules_violation'
        if semantic_reply_issues(review, db.get(Product, review.product_id), draft.text) or validate_reply(draft.text) != draft.text:
            return 'content_check'
    except ValueError:
        return 'content_check'
    return ''


def owner_review_reason(db, review, draft, rules):
    """Human approval can replace an unavailable automated verdict, never known content errors."""
    if (review.marketplace != 'wb' or review.is_answered or
            review.status not in ('draft_ready', 'manual_review')):
        return 'not_ready'
    try:
        if validate_reply(draft.text) != draft.text or semantic_reply_issues(review, db.get(Product, review.product_id), draft.text):
            return 'content_check'
    except ValueError:
        return 'content_check'
    previous = (draft.quality or {}).get('owner_rules') or {}
    if (previous.get('checked') and previous.get('passed') is False and
            not previous.get('technical_error') and previous.get('fingerprint') == fingerprint(rules)):
        return 'rules_violation'
    return ''


def approval_snapshot(review, rules):
    return {'text': review.text, 'rating': review.rating, 'product_id': review.product_id,
            'manual': review.manual, 'risk': review.risk, 'safety_critical': review.safety_critical,
            'rules': fingerprint(rules)}


def owner_approved_item(db, parent_id, actor_id, review, draft, item, rules):
    """Server-only authorization tied to an owner-confirmed frozen manifest and current inputs."""
    parent, actor = db.get(Action, parent_id), db.get(User, actor_id)
    if not (parent and parent.kind == PARENT_KIND and parent.status == 'sending' and
            parent.payload.get('include_excluded') and parent.result.get('excluded_ack') and
            parent.result.get('confirmed_by') == actor_id and actor and actor.enabled and actor.role == 'owner' and
            parent.payload['account_id'] == review.wb_account_id and
            item.get('approval_snapshot') == approval_snapshot(review, rules) and
            draft.id == item['draft_id'] and draft.review_id == review.id and
            draft.revision == item['revision'] and draft.text == item['text']):
        return False
    # A client cannot forge an approval by adding fields to an ordinary publish action.
    manifests = db.scalars(select(Action).where(Action.kind == MANIFEST_KIND,
        Action.payload['parent_id'].as_string() == parent_id,
        Action.payload['start_index'].as_integer() <= item.get('manifest_index', -1),
        Action.payload['end_index'].as_integer() > item.get('manifest_index', -1)))
    return (any(item in row.payload['items'] for row in manifests) and
            not owner_review_reason(db, review, draft, rules))


def prepare_step(job_id):
    started, rules_cache = time.monotonic(), {}
    with Session() as db:
        job = db.get(Job, job_id)
        parent = db.get(Action, job.result['parent_id'])
        while time.monotonic() - started < SLICE_SECONDS:
            db.refresh(job); db.refresh(parent)
            if job.cancel_requested or parent.status != 'preparing':
                job.status = 'cancelled'; db.commit(); return
            cursor = job.result.get('cursor', '')
            if 'exclusion_legacy_cursor' not in parent.payload:
                parent.payload = {**parent.payload, 'exclusion_legacy_cursor': cursor}
            rows = db.execute(select(Review, Draft).join(Draft, Draft.review_id == Review.id).where(
                Review.wb_account_id == parent.payload['account_id'], Review.marketplace == 'wb',
                Review.is_answered == False, Review.id > cursor).order_by(Review.id).limit(PAGE_SIZE)).all()
            if not rows:
                parent.status = 'pending' if parent.payload['total'] else 'empty'
                parent.payload = {**parent.payload, 'prepared_at': now()}
                job.status, job.progress = 'completed', 100
                db.commit(); return
            items, excluded, included_excluded = [], [], 0
            for review, draft in rows:
                if review.product_id not in rules_cache:
                    rules_cache[review.product_id] = matching_owner_rules(db, db.get(Product, review.product_id))
                try:
                    allowed = eligible(db, review, draft, rules_cache[review.product_id])
                except ValueError:
                    allowed = False
                ordinary = allowed
                if parent.payload.get('include_excluded'):
                    allowed = not owner_review_reason(db, review, draft, rules_cache[review.product_id])
                attempted = db.scalar(select(Publication.id).where(Publication.review_id == review.id)) if allowed else None
                if allowed and not attempted:
                    item = {'review_id': review.id, 'draft_id': draft.id, 'revision': draft.revision,
                            'text': draft.text, 'rating': review.rating, 'manual': review.manual, 'kind': 'review'}
                    if parent.payload.get('include_excluded'):
                        item.update(approval_snapshot=approval_snapshot(review, rules_cache[review.product_id]),
                                    manifest_index=parent.payload['total'] + len(items),
                                    owner_review=not ordinary)
                        included_excluded += int(not ordinary)
                    items.append(item)
                else:
                    excluded.append({'review_id': review.id, 'reason': 'already_attempted' if attempted else
                        (owner_review_reason(db, review, draft, rules_cache[review.product_id]) if parent.payload.get('include_excluded') else
                         exclusion_reason(db, review, draft, rules_cache[review.product_id])), 'at': now(), 'retrospective': False})
            if items:
                db.add(Action(id=str(uuid4()), kind=MANIFEST_KIND, status='staged', payload={
                    'parent_id': parent.id, 'account_id': parent.payload['account_id'], 'items': items,
                    'start_index': parent.payload['total'], 'end_index': parent.payload['total'] + len(items)},
                    result={'next_index': 0, 'operations': {}}))
            db.refresh(parent, with_for_update=True)
            from .bulk_exclusions import save_chunk
            save_chunk(db, parent, excluded)
            parent.payload = {**parent.payload, 'exclusion_legacy_cursor': parent.payload.get('exclusion_legacy_cursor', cursor), 'total': parent.payload['total'] + len(items),
                'scanned': parent.payload['scanned'] + len(rows),
                'included_excluded': parent.payload.get('included_excluded', 0) + included_excluded,
                'excluded': parent.payload['excluded'] + len(rows) - len(items)}
            job.result = {**job.result, 'cursor': rows[-1][0].id, 'processed': parent.payload['scanned'],
                          'message': 'Собираем проверенные ответы со всех страниц', 'updated_at': now()}
            db.commit()
        job.status = 'queued'; db.commit()


async def send_step(job_id):
    from .services import confirm_action
    from .config import REAL_PUBLISH
    started, count = time.monotonic(), 0
    with Session() as db:
        job = db.get(Job, job_id)
        parent = db.get(Action, job.result['parent_id'])
        while count < 10 and time.monotonic() - started < SLICE_SECONDS:
            # Other sessions can revoke access or change modes between writes.
            db.expire_all()
            db.refresh(job); db.refresh(parent)
            if job.cancel_requested or parent.status != 'sending':
                job.status = 'cancelled'; db.commit(); return
            store = db.get(StoreProfile, parent.payload['account_id'])
            actor = db.get(User, parent.result['confirmed_by'])
            permissions = (actor.permissions or []) if actor else []
            permitted = (actor and actor.enabled and
                (actor.role == 'owner' or '*' in permissions or 'wb:operate' in permissions
                 or not permissions and actor.position == 'Менеджер WB') and
                (actor.role == 'owner' or '*' in (actor.store_ids or []) or parent.payload['account_id'] in (actor.store_ids or [])))
            if (not store or store.provider != 'wb' or not permitted or
                setting(db, 'safe_mode', False) or setting(db, 'test_mode', True) or not REAL_PUBLISH):
                parent.status, job.status = 'needs_review', 'completed'
                parent.result = {**parent.result, 'message': 'Отправка остановлена: проверьте магазин, права и рабочий режим публикации. После проверки подготовьте новый пакет.'}
                db.commit(); return
            manifest = db.scalar(select(Action).where(Action.kind == MANIFEST_KIND, Action.status == 'staged',
                Action.payload['parent_id'].as_string() == parent.id).order_by(Action.id).limit(1))
            if not manifest:
                parent.status = 'needs_review' if parent.result.get('uncertain') or parent.result.get('skipped') else 'completed'
                job.status, job.progress = 'completed', 100
                db.commit(); return
            index = manifest.result['next_index']
            item = manifest.payload['items'][index]
            operations = dict(manifest.result.get('operations', {}))
            aid = operations.get(str(index))
            if not aid:
                aid = str(uuid4()); operations[str(index)] = aid
                db.add(Action(id=aid, kind='publish', payload={'items': [item], 'excluded': [],
                    'test_mode': False, 'bulk_parent': parent.id}))
                manifest.result = {**manifest.result, 'operations': operations}
                db.commit()
            child = db.get(Action, aid)
            outcome, detail = 'skipped', ''
            try:
                review = db.get(Review, item['review_id'])
                if not review or review.wb_account_id != parent.payload['account_id'] or review.marketplace != 'wb':
                    raise ValueError('Магазин отзыва изменился')
                if child.status == 'pending':
                    draft = db.get(Draft, item['draft_id'])
                    rules = matching_owner_rules(db, db.get(Product, review.product_id))
                    owner_approval = bool(draft and owner_approved_item(db, parent.id, parent.result['confirmed_by'], review, draft, item, rules))
                    if not draft or not (owner_approval if parent.payload.get('include_excluded') else eligible(db, review, draft, rules)):
                        raise ValueError('Ответ больше не готов к массовой публикации')
                    if owner_approval:
                        await confirm_action(db, aid, True, parent.result['confirmed_by'], bulk_approval=parent.id)
                    else:
                        await confirm_action(db, aid, False, parent.result['confirmed_by'])
                db.refresh(child)
                statuses = [row['status'] for row in (child.result or {}).get('items', [])]
                outcome = 'published' if statuses == ['published'] else 'uncertain' if child.status in ('executing', 'needs_review') else 'skipped'
            except ValueError as error:
                db.rollback(); detail = str(error)[:300]
                child = db.get(Action, aid)
                publication = db.scalar(select(Publication).where(Publication.action_id == aid))
                outcome = 'uncertain' if publication else 'skipped'
                if child.status == 'pending':
                    child.status, child.result = 'cancelled', {'message': detail}
            # The child and its publication slot survive a crash before this
            # checkpoint. Re-reading that child never repeats an uncertain write.
            db.refresh(parent); db.refresh(manifest); db.refresh(job)
            parent.result = {**parent.result, 'processed': parent.result.get('processed', 0) + 1,
                outcome: parent.result.get(outcome, 0) + 1}
            manifest.result = {**manifest.result, 'next_index': index + 1}
            if index + 1 == len(manifest.payload['items']):
                manifest.status = 'completed'
            job.result = {**job.result, 'processed': parent.result['processed'],
                'message': 'Отправляем подтверждённые ответы в Wildberries', 'updated_at': now()}
            job.progress = min(99, round(parent.result['processed'] / max(1, parent.payload['total']) * 100))
            db.commit(); count += 1
            if outcome in ('published', 'uncertain'):
                await publication_pause()
        job.status = 'queued'; db.commit()
