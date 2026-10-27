"""Inspectable exclusion records. Diagnostics never approve or publish a reply."""
import time
from uuid import uuid4
from sqlalchemy import select, func, text, String
from .db import Session, Action, Job, Review, Draft, Product, Publication, Memory, now

KIND = 'wb_publish_excluded'
AUDIT_KIND = 'wb_exclusions_audit'
LABELS = {
    'check_timeout': 'Истекло время проверки правил',
    'check_error': 'Сервис проверки вернул ошибку',
    'rules_changed': 'Ответ не проверен по текущим правилам',
    'rules_unchecked': 'Проверка правил не завершена',
    'rules_violation': 'Найдены нарушения правил',
    'low_rating': 'Низкая оценка: 1–3 звезды',
    'safety': 'Ручная проверка или опасная ситуация',
    'not_ready': 'Статус ответа не готов к массовой отправке',
    'text_quality': 'Не пройдена проверка текста',
    'content_check': 'Текст требует дополнительной проверки',
    'already_attempted': 'Публикация уже начата или требует сверки',
    'changed_since_scan': 'Состояние изменилось после отбора',
}


def save_chunk(db, parent, items):
    if not items:
        return
    counts = dict(parent.payload.get('exclusion_counts', {}))
    for item in items:
        counts[item['reason']] = counts.get(item['reason'], 0) + 1
    parent.payload = {**parent.payload, 'exclusion_counts': counts}
    db.add(Action(id=str(uuid4()), kind=KIND, status='completed', payload={
        'parent_id': parent.id, 'account_id': parent.payload['account_id'], 'items': items}, result={}))


def start_audit(db, parent, actor_id):
    parent = db.scalar(select(Action).where(Action.id == parent.id).with_for_update().execution_options(populate_existing=True))
    jid = parent.payload.get('exclusions_job_id')
    if jid:
        job = db.get(Job, jid)
        if job and job.status in ('failed', 'cancelled', 'interrupted'):
            job.status, job.cancel_requested = 'queued', False
            job.result = {**job.result, 'failure_attempts': 0, 'retry_at': 0}
            db.commit()
        return  # Never rescan a committed diagnostic range on repeated clicks.
    preparation = db.get(Job, parent.payload['prepare_job_id'])
    cutoff = parent.payload.get('exclusion_legacy_cursor', (preparation.result or {}).get('cursor', '') if preparation else '')
    if not cutoff:
        return
    jid = str(uuid4())
    parent.payload = {**parent.payload, 'exclusion_legacy_cursor': cutoff, 'exclusions_job_id': jid}
    db.add(Job(id=jid, kind=AUDIT_KIND, result={'parent_id': parent.id,
        'account_id': parent.payload['account_id'], 'requested_by': actor_id, 'cursor': '', 'cutoff': cutoff}))
    db.commit()


def audit_step(job_id):
    from .bulk_publication import MANIFEST_KIND, exclusion_reason
    from .owner_reply_rules import matching_owner_rules
    started = time.monotonic()
    with Session() as db:
        job = db.get(Job, job_id)
        parent = db.get(Action, job.result['parent_id'])
        # Old batches saved accepted IDs but no reasons for the remaining IDs.
        # Reconstruct membership from that immutable list, then diagnose NOW.
        accepted = {item['review_id'] for row in db.scalars(select(Action).where(
            Action.kind == MANIFEST_KIND, Action.payload['parent_id'].as_string() == parent.id))
            for item in row.payload['items'] if item['review_id'] <= job.result['cutoff']}
        rule_rows = list(db.scalars(select(Memory).where(Memory.enabled == True).order_by(Memory.id)))
        rules_cache = {}
        while time.monotonic() - started < 30:
            db.refresh(job)
            if job.cancel_requested:
                job.status = 'cancelled'; db.commit(); return
            rows = db.execute(select(Review, Draft).join(Draft, Draft.review_id == Review.id).where(
                Review.wb_account_id == job.result['account_id'], Review.marketplace == 'wb',
                Review.id > job.result.get('cursor', ''), Review.id <= job.result['cutoff'],
                Review.is_answered == False).order_by(Review.id).limit(100)).all()
            if not rows:
                job.status, job.progress = 'completed', 100; db.commit(); return
            attempted = set(db.scalars(select(Publication.review_id).where(Publication.review_id.in_([row[0].id for row in rows]))))
            items = []
            for review, draft in rows:
                if review.id in accepted:
                    continue
                if review.product_id not in rules_cache:
                    rules_cache[review.product_id] = matching_owner_rules(db, db.get(Product, review.product_id), rule_rows=rule_rows)
                reason = exclusion_reason(db, review, draft, rules_cache[review.product_id])
                if review.id in attempted:
                    reason = 'already_attempted'
                items.append({'review_id': review.id, 'reason': reason or 'changed_since_scan', 'at': now(), 'retrospective': True})
            db.refresh(parent, with_for_update=True)
            save_chunk(db, parent, items)
            job.result = {**job.result, 'cursor': rows[-1][0].id,
                'processed': job.result.get('processed', 0) + len(rows), 'updated_at': now()}
            db.commit()
        job.status = 'queued'; db.commit()


def exclusion_rows(db, parent_id):
    # Only fixed SQL fragments vary by dialect; IDs and filters are bound values.
    if db.bind.dialect.name == 'postgresql':
        sql = """select e.value->>'review_id' as review_id, e.value->>'reason' as reason,
            e.value->>'at' as checked_at, e.value->>'retrospective' as retrospective
            from actions a cross join lateral jsonb_array_elements(a.payload->'items') e(value)
            where a.kind=:excluded_kind and a.payload->>'parent_id'=:parent_id"""
    else:
        sql = """select json_extract(e.value,'$.review_id') as review_id,
            json_extract(e.value,'$.reason') as reason, json_extract(e.value,'$.at') as checked_at,
            json_extract(e.value,'$.retrospective') as retrospective
            from actions a, json_each(a.payload,'$.items') e
            where a.kind=:excluded_kind and json_extract(a.payload,'$.parent_id')=:parent_id"""
    return text(sql).bindparams(excluded_kind=KIND, parent_id=parent_id).columns(
        review_id=String, reason=String, checked_at=String, retrospective=String).subquery('exclusions')


def page(db, parent, reason='', q='', sort='newest', offset=0):
    from .services import review_items
    if reason and reason not in LABELS:
        raise ValueError('Неизвестная причина исключения')
    if sort not in ('newest', 'oldest', 'rating_asc', 'rating_desc'):
        raise ValueError('Неизвестный порядок сортировки')
    entries = exclusion_rows(db, parent.id)
    statement = select(Review, entries.c.reason, entries.c.checked_at, entries.c.retrospective).join(
        entries, entries.c.review_id == Review.id).join(Product).where(Review.wb_account_id == parent.payload['account_id'])
    if reason:
        statement = statement.where(entries.c.reason == reason)
    if q:
        statement = statement.where(Review.text.contains(q, autoescape=True) | Product.name.contains(q, autoescape=True))
    total = db.scalar(statement.with_only_columns(func.count()).order_by(None)) or 0
    ordering = {'newest': Review.created_at.desc(), 'oldest': Review.created_at,
                'rating_asc': Review.rating, 'rating_desc': Review.rating.desc()}[sort]
    rows = db.execute(statement.order_by(ordering, Review.id).offset(offset).limit(20)).all()
    items = review_items(db, [row[0] for row in rows])
    for item, row in zip(items, rows):
        item['exclusion'] = {'reason': row[1], 'label': LABELS.get(row[1], row[1]),
            'checked_at': row[2], 'retrospective': str(row[3]).lower() in ('true', '1')}
    return {'items': items, 'total': total, 'offset': offset, 'has_more': offset + len(items) < total}


def summary(db, parent):
    jid = parent.payload.get('exclusions_job_id')
    job = db.get(Job, jid) if jid else None
    counts = parent.payload.get('exclusion_counts', {})
    preparation = db.get(Job, parent.payload['prepare_job_id'])
    legacy = parent.payload.get('exclusion_legacy_cursor', (preparation.result or {}).get('cursor', '') if preparation else '')
    return {'counts': counts, 'labels': LABELS, 'diagnosed': sum(counts.values()),
        'audit_status': job.status if job else 'not_started' if legacy else 'not_needed',
        'audit_error': (job.result or {}).get('detail', '') if job and job.status == 'failed' else ''}
